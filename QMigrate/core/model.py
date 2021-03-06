# -*- coding: utf-8 -*-
"""
Module to produce cartesian gridded traveltime look-up tables.

"""

import math
import warnings
import pickle
import struct
from copy import copy
import os

import skfmm
import pyproj
import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator, griddata, interp1d


def _cart2sph_np_array(xyz):
    # theta_phi_r = _cart2sph_np_array(xyz)
    tpr = np.zeros(xyz.shape)
    xy = xyz[:, 0] ** 2 + xyz[:, 1] ** 2
    tpr[:, 0] = np.arctan2(xyz[:, 1], xyz[:, 0])
    tpr[:, 1] = np.arctan2(xyz[:, 2], np.sqrt(xy))
    tpr[:, 2] = np.sqrt(xy + xyz[:, 2] ** 2)
    return tpr


def _cart2sph_np(xyz):
    # theta_phi_r = _cart2sph_np(xyz)
    if xyz.ndim == 1:
        tpr = np.zeros(3)
        xy = xyz[0] ** 2 + xyz[1] ** 2
        tpr[0] = np.arctan2(xyz[1], xyz[0])
        tpr[1] = np.arctan2(xyz[2], np.sqrt(xy))
        tpr[2] = np.sqrt(xy + xyz[2] ** 2)
    else:
        tpr = np.zeros(xyz.shape)
        xy = xyz[:, 0] ** 2 + xyz[:, 1] ** 2
        tpr[:, 0] = np.arctan2(xyz[:, 1], xyz[:, 0])
        tpr[:, 1] = np.arctan2(xyz[:, 2], np.sqrt(xy))
        tpr[:, 2] = np.sqrt(xy + xyz[:, 2] ** 2)
    return tpr


def _sph2cart_np(tpr):
    # xyz = _sph2cart_np(theta_phi_r)
    if tpr.ndim == 1:
        xyz = np.zeros(3)
        xyz[0] = tpr[2] * np.cos(tpr[1]) * np.cos(tpr[0])
        xyz[1] = tpr[2] * np.cos(tpr[1]) * np.sin(tpr[0])
        xyz[2] = tpr[2] * np.sin(tpr[1])
    else:
        xyz = np.zeros(tpr.shape)
        xyz[:, 0] = tpr[:, 2] * np.cos(tpr[:, 1]) * np.cos(tpr[:, 0])
        xyz[:, 1] = tpr[:, 2] * np.cos(tpr[:, 1]) * np.sin(tpr[:, 0])
        xyz[:, 2] = tpr[:, 2] * np.sin(tpr[:, 1])
    return xyz


def _coord_transform_np(p1, p2, loc):
    xyz = np.zeros(loc.shape)
    if loc.ndim == 1:
        xyz[0], xyz[1], xyz[2] = pyproj.transform(p1, p2,
                                                  loc[0],
                                                  loc[1],
                                                  loc[2])
    else:
        xyz[:, 0], xyz[:, 1], xyz[:, 2] = pyproj.transform(p1, p2,
                                                           loc[:, 0],
                                                           loc[:, 1],
                                                           loc[:, 2])
    return xyz


def _proj(**kwargs):
    projection = kwargs.get("projection")
    if projection == "WGS84":
        proj = "+proj=longlat +ellps=WGS84 +datum=WGS84 +no_defs"
    if projection == "NAD27":
        proj = "+proj=longlat +ellps=clrk66 +datum=NAD27 +no_defs"
    if projection == "UTM":
        zone = _utm_zone(kwargs.get("longitude"))
        proj = "+proj=utm +zone={0:d} +datum=WGS84 +units=m +no_defs"
        proj = proj.format(zone)
    if projection == "LCC":
        lon0 = kwargs.get("lon0")
        lat0 = kwargs.get("lat0")
        parallel_1 = kwargs.get("parallel_1")
        parallel_2 = kwargs.get("parallel_2")
        proj = "+proj=lcc +lon_0={} +lat_0={} +lat_1={} +lat_2={} +datum=WGS84 +units=m +no_defs"
        proj = proj.format(float(lon0), float(lat0),
                           float(parallel_1), float(parallel_2))
    if projection == "TM":
        lon = kwargs.get("lon")
        lat = kwargs.get("lat")
        proj = "+proj=tmerc +lon_0={} +lat_0={} +datum=WGS84 +units=m +no_defs"
        proj = proj.format(float(lon), float(lat))

    return pyproj.Proj(proj)


def _utm_zone(longitude):
    return (int(1 + math.fmod((longitude + 180.0) / 6.0, 60)))


# def _proj_nlloc_simple(latOrg,lonOrg,rotAngle):
#     x = (long - longOrig) * 111.111 * cos(lat_radians)
#     y = (lat - latOrig) * 111.111
#     lat = latOrig + y / 111.111
#     long = longOrig + x / (111.111 * cos(lat_radians))
#     x=(lon)


def bilinear_interp(pos, gridspec, grid):
    """
    Do bi-linear interpolation between 4 data points on the input 2-D LUT to
    calculate the traveltime to nodes on the 3-D grid. 

    """
    if len(pos) == 2:
        x = pos[0]
        z = pos[1]
    else:
        x = pos[:, 0]
        z = pos[:, 1]
    _, _ = gridspec[0]
    x0, z0 = gridspec[1]
    dx, dz = gridspec[2]
    # nzy = nx * nz

    # get the position of the nearest node
    i = np.floor((x - x0)/dx).astype(np.int)
    k = np.floor((z - z0)/dz).astype(np.int)

    # get fractional distance of earthquake along each axis
    xd = (x / dx) - np.floor(x / dx)
    zd = (z / dz) - np.floor(z / dz)

    if np.all(zd == 0):
        # there is no interpolation in Z
        c0 = grid[i, k]
        c1 = grid[i+1, k]

        # do the interpolation along x
        c = c0*(1 - xd) + c1 * xd
    else:

        # Do bi-linear interpolation
        # get the 4 data points of the surrounding square
        c00 = grid[i, k]
        c10 = grid[i + 1, k]
        # k[(k==nz-1) & (zd==0.)] -=1
        c11 = grid[i + 1, k + 1]
        c01 = grid[i, k + 1]

        # do the interpolation along y
        c0 = c00 * (1 - xd) + c10 * xd
        c1 = c01 * (1 - xd) + c11 * xd

        # do the interpolation along z
        c = c0*(1 - zd) + c1 * zd

    return c


def read_2d_nlloc(froot):
    """
    Read the NonLinLoc travel time grids

    HEADER
    nx ny nz x0 y0 z0 dx dy dz
    2 101 111  0.000000 0.000000 -2.000000  0.200000 0.200000 0.200000 SLOW_LEN FLOAT
    TRANSFORM  TRANS_MERC RefEllipsoid WGS-84  LatOrig 8.000000  LongOrig 38.000000  RotCW 0.000000
    """

    with open(froot + ".hdr", "r") as fid:
        line = fid.readline().split()
        nx = int(line[0])
        ny = int(line[1])
        nz = int(line[2])
        x0 = float(line[3])
        y0 = float(line[4])
        z0 = float(line[5])
        dx = float(line[6])
        dy = float(line[7])
        dz = float(line[8])
        line = fid.readline().split()
        # st_name = line[0]
        st_x = float(line[1])
        st_y = float(line[2])
        st_z = float(line[3])

    npts = nx * ny * nz
    with open(froot + ".buf", "rb") as fid:
        buf = fid.read(npts * 4)
        data = struct.unpack("f" * npts, buf)

    data = np.reshape(data, (nx, ny, nz), order="C")

    distance_x = x0 + (np.linspace(0, nx - 1, nx) * dx)
    distance_y = y0 + (np.linspace(0, ny - 1, ny) * dy)
    distance_z = z0 + (np.linspace(0, nz - 1, nz) * dz)

    X, Y, Z = np.meshgrid(distance_x, distance_y, distance_z, indexing="ij")

    distance = np.sqrt(np.square(X) + np.square(Y) + np.square(Z))

    return data, (X, Y, Z, distance), (st_x, st_y, st_z), \
        [[nx, ny, nz], [x0, y0, z0], [dx, dy, dz]]


def grid_string(max_dist, max_depth, min_depth, dx):
    max_x = int(np.ceil(max_dist / dx)) + 5
    max_z = int(np.ceil((max_depth - min_depth) / dx)) + 5

    string = "2 {0:d} {1:d} 0.0 0.0 {2:f} {3:f} {3:f} {3:f}"
    return string.format(max_x, max_z, min_depth, dx)


def vgradient(i, vmodel):
    d_depth = vmodel["depth"][i+1] - vmodel["depth"][i]
    d_vel_p = vmodel["vp"][i+1] - vmodel["vp"][i]
    d_vel_s = vmodel["vs"][i+1] - vmodel["vs"][i]

    return d_vel_p / d_depth, d_vel_s / d_depth


def vmodel_string(vmodel, block):

    string = "LAYER  {0:f} {1:f} {3:f} {2:f} {4:f} 0.0 0.0"

    out = []

    nlayer = len(vmodel)
    i = 0
    while i < nlayer:
        if not block:
            try:
                gradientp, gradients = vgradient(i, vmodel)
            except KeyError:
                gradientp, gradients = 0., 0.
        else:
            gradientp = 0.
            gradients = 0.
        out.append(string.format(vmodel["depth"][i] / 1000.,
                                 vmodel["vp"][i] / 1000.,
                                 vmodel["vs"][i] / 1000.,
                                 gradientp, gradients))
        i += 1

    return "\n".join(out)


def write_control_file(x, y, z, name, max_dist,
                       vmodel, depth_limits, phase="P",
                       dx=0.2, block_model=True):
    control_string = """CONTROL 0 54321
TRANS NONE
#TRANS LAMBERT WGS-84 8.0 38.0 8.2 8.4 0.0
#TRANS TRANS_MERC WGS-84 8.0 38.0 0.0

VGOUT ./model/layer
VGTYPE {phase:s}

#VGGRID 2 101 111 0.0 0.0 -2.0 0.2 0.2 0.2 SLOW_LEN
VGGRID {grid:s} SLOW_LEN

{vmodel:s}
#LAYER  0.0 3.0 0.0 0.0 0.0 0.0 0.0
#LAYER  2.0 4.0 0.0 0.0 0.0 0.0 0.0
#LAYER 10.0 6.0 0.0 0.0 0.0 0.0 0.0
#LAYER 15.0 6.5 0.0 0.0 0.0 0.0 0.0
#LAYER  5.0 5.0 0.0 0.0 0.0 0.0 0.0
#LAYER 20.0 7.3 0.0 0.0 0.0 0.0 0.0

GTFILES ./model/layer ./time/layer {phase:s}
GTMODE GRID2D ANGLES_NO

#GTSRCE ST01 LATLON 8.1 38.1 0.0 0.0
GTSRCE {name:s} XYZ {x:f} {y:f} {z:f} 0.0

GT_PLFD 1.0E-3 0
                    """
    outstring = control_string.format(phase=phase,
                                      grid=grid_string(max_dist,
                                                       depth_limits[1],
                                                       depth_limits[0], dx),
                                      vmodel=vmodel_string(vmodel,
                                                           block_model),
                                      name=name, y=y, x=x, z=z)

    with open("./control.in", "w") as fid:
        fid.write(outstring)

    # print(outstring)
    return


def eikonal(ix, iy, iz, dxi, dyi, dzi, V, S):
    """
    Travel-Time formulation using a simple eikonal method.

    Requires the skifmm python package.

    Parameters
    ----------
    ix : array-like
        Number of cells in X-direction

    iy : array-like
        Number of cells in Y-direction

    iz : array-like
        Number of cells in Z-direction

    dxi :
        Cell length in X-direction

    dyi :
        Cell length in Y-direction

    dzi :
        Cell length in Z-direction

    V : array-like
        Contains the speed of interface propagation at each point in the
        domain

    S : array-like
        ???

    Returns
    -------
    t : array-like, same shape as phi
        Contains the travel time from the zero contour (zero level set) of phi
        to each point in the array given the scalar velocity field speed. If
        the input array speed has values less than or equal to zero the return
        value will be a masked array.

    """

    phi = -np.ones(ix.shape)
    indx = np.argmin(abs((ix - S[:, 0]))
                     + abs((iy - S[:, 1]))
                     + abs((iz - S[:, 2])))
    phi[np.unravel_index(indx, ix.shape)] = 1.0

    t = skfmm.travel_time(phi, V, dx=[dxi, dyi, dzi])
    return t


class Grid3D(object):
    """
    3D grid class

    Attributes
    ----------
    cell_count : array-like
        Number of cells in each dimension of the grid

    cell_size : array-like
        Size of a cell in each dimension of the grid

    azimuth : float
        Angle between northing vertical plane and grid y-z plane

    dip : float
        Angle between horizontal plane and grid x-y plane

    Methods
    -------
    lonlat_centre(longitude, latitude)
        Define the longitude and latitude of the centre of the grid

    nlloc_grid_centre(origin_lon, origin_lat)
        Define the centre of the grid from NonLinLoc file parameters

    """

    def __init__(self, cell_count, cell_size, azimuth, dip, sort_order="C"):
        """
        Class initialisation

        Parameters
        ----------
        cell_count : array-like
            Number of cells in each dimension of the grid

        cell_size : array-like
            Size of a cell in each dimension of the grid

        azimuth : float
            Angle between northing vertical plane and grid y-z plane

        dip : float
            Angle between horizontal plane and grid x-y plane

        sort_order : str
            Determines whether the multi-index should be viewed as indexing in
            row-major (C-style) or column-major (Fortran-style) order.

        longitude : float
            Longitude coordinate of the grid centre

        latitude : float
            Latitude coordinate of the grid centre

        elevation : float
            Elevation coordinate of the top grid layer (units: m)

        grid_centre : array-like
            Array containing coordinates of the grid centre

        grid_proj : pyproj object
            Grid space projection

        coord_proj : pyproj object
            Coordinate space projection

        """

        self._coord_proj = None
        self._grid_proj = None
        self._longitude = None
        self._latitude = None
        self._grid_centre = [0.0, 0.0, 0.0]

        self.cell_count = cell_count
        self.cell_size = cell_size
        self.elevation = 0
        self.azimuth = azimuth
        self.dip = dip
        self.sort_order = sort_order
        self.UTM_zones_different = False
        self.lcc_standard_parallels = (0.0, 0.0)

    def projections(self, grid_proj_type, coord_proj=None):
        """

        """
        if coord_proj and self._coord_proj is None:
            self.coord_proj = _proj(projection=coord_proj)
        elif self._coord_proj is None:
            self.coord_proj = _proj(projection="WGS84")

        if grid_proj_type == "UTM":
            self.grid_proj = _proj(projection=grid_proj_type,
                                   longitude=self.longitude)
        elif grid_proj_type == "LCC":
            self.grid_proj = _proj(projection=grid_proj_type,
                                   lon0=self.longitude,
                                   lat0=self.latitude,
                                   parallel_1=self.lcc_standard_parallels[0],
                                   parallel_2=self.lcc_standard_parallels[1])
        elif grid_proj_type == "TM":
            self.grid_proj = _proj(projection=grid_proj_type,
                                   lon=self.longitude,
                                   lat=self.latitude)
        else:
            msg = "Projection type must be specified.\n"
            msg += "QuakeMigrate currently supports:\n"
            msg += "        UTM (Universal Transverse Mercator)\n"
            msg += "        LCC (Lambert Conical Conformic)\n"
            msg += "        TM (Transverse Mercator)"
            raise Exception(msg)

    def lonlat_centre(self, longitude=None, latitude=None):
        """
        Define the centre of the 3D grid in geographical coordinates

        Parameters
        ----------
        longitude : float
            Geographical longitude of grid centre

        latitude : float
            Geographical latitude of grid centre

        """

        if longitude:
            self.longitude = longitude
        if latitude:
            self.latitude = latitude

    def nlloc_grid_centre(self, origin_lon, origin_lat):
        """

        Parameters
        ----------
        origin_lon : float
            Geographical longitude of grid origin

        origin_lat : float
            Geographical latitude of grid origin

        """

        self.coord_proj = _proj(projection="WGS84")
        # if _utm_zone(self.longitude) != _utm_zone(origin_lon):
        #     self.grid_proj = _proj(projection="UTM", longitude=self.longitude)
        if self.NLLoc_proj != "NONE":
            self.grid_proj = self._nlloc_grid_proj()
        grid_origin = self.xy2lonlat(origin_lon, origin_lat, inverse=True)
        x = grid_origin[0] + self.centre[0]
        y = grid_origin[1] + self.centre[1]
        self.longitude, self.latitude = self.xy2lonlat(x, y)
        self._update_grid_centre()

    def _update_grid_centre(self):
        x, y = pyproj.transform(self.coord_proj, self.grid_proj,
                                self.longitude, self.latitude)

        self.grid_centre = [x, y, self.elevation - ((self.cell_count[2] - 1)
                                                    * self.cell_size[2]) / 2]

    def _update_coord_centre(self):
        lon, lat = pyproj.transform(self.grid_proj, self.coord_proj,
                                    self.grid_centre[0], self.grid_centre[1])
        self.longitude = lon
        self.latitude = lat

    def _nlloc_grid_proj(self):
        if self.NLLoc_proj:
            if self.NLLoc_proj == "SIMPLE":
                print("ERROR -- simple not yet supported")
            elif self.NLLoc_proj == "LAMBERT":
                return _proj(projection="LCC", lon0=self.NLLoc_MapOrg[0],
                             lat0=self.NLLoc_MapOrg[1],
                             parallel_1=self.NLLoc_MapOrg[4],
                             parallel_2=self.NLLoc_MapOrg[5])
            elif self.NLLoc_proj == "TRANS_MERC":
                return _proj(projection="TM", lon=self.NLLoc_MapOrg[0],
                             lat=self.NLLoc_MapOrg[1])

    @property
    def cell_count(self):
        """
        Get and set the number of cells in each dimension of the grid.

        """

        return self._cell_count

    @cell_count.setter
    def cell_count(self, value):
        value = np.array(value, dtype="int32")
        if value.size == 1:
            value = np.repeat(value, 3)
        else:
            assert (value.shape == (3,)), "Cell count must be an n by 3 array."
        assert (np.all(value > 0)), "Cell count must be greater than [0]"
        self._cell_count = value

    @property
    def cell_size(self):
        """
        Get and set the size of a cell in each dimension of the grid.

        """

        return self._cell_size

    @cell_size.setter
    def cell_size(self, value):
        """
        Get and set the cell size in each dimension of the grid.

        """
        value = np.array(value, dtype="float64")
        if value.size == 1:
            value = np.repeat(value, 3)
        else:
            assert (value.shape == (3,)), "Cell size must be an n by 3 array."
        assert (np.all(value > 0)), "Cell size must be greater than [0]"
        self._cell_size = value

    @property
    def longitude(self):
        """
        Longitude of the grid centre.

        """

        return self._longitude

    @longitude.setter
    def longitude(self, value):
        # Add tests for suitable longitude
        self._longitude = value
        if self._grid_proj and self.coord_proj and self.latitude:
            self._update_grid_centre()

    @property
    def latitude(self):
        """Get and set the latitude of the grid centre"""

        return self._latitude

    @latitude.setter
    def latitude(self, value):
        # Add tests for suitable latitude
        self._latitude = value
        if self._grid_proj and self.coord_proj and self.longitude:
            self._update_grid_centre()

    @property
    def elevation(self):
        """
        Get the elevation of the grid centre

        """

        return self._elevation

    @elevation.setter
    def elevation(self, value):
        # Add tests for suitable elevation
        self._elevation = value
        if (self._grid_proj and self._coord_proj and
                self.longitude and self.latitude):
            self._update_grid_centre()

    @property
    def grid_centre(self):
        """Get and set the centre of the grid"""
        # x, y = pyproj.transform(self.grid_proj, self.coord_proj,
        #                         self.longitude, self.latitude)
        # self._grid_centre = [x, y, self.elevation]

        return self._grid_centre

    @grid_centre.setter
    def grid_centre(self, value):
        value = np.array(value, dtype="float64")
        assert (value.shape == (3,)), "Grid centre must be [x, y, z] array."
        self._grid_centre = value

    @property
    def grid_proj(self):
        """
        Get and set the grid projection (defaults to WGS84)

        """

        if self._grid_proj is None:
            msg = "Grid projection has not been set: assuming WGS84"
            warnings.warn(msg)
            return _proj(projection="UTM", longitude=self.longitude)
        else:
            return self._grid_proj

    @grid_proj.setter
    def grid_proj(self, value):
        self._grid_proj = value
        if self._coord_proj and self.longitude and self.latitude:
            self._update_grid_centre()

    @property
    def coord_proj(self):
        """Get and set the coordinate projection"""
        return self._coord_proj

    @coord_proj.setter
    def coord_proj(self, value):
        self._coord_proj = value
        if self._grid_proj and self.longitude and self.latitude:
            self._update_coord_centre()

    def xy2lonlat(self, x, y, inverse=False):
        x = np.array(x)
        y = np.array(y)
        if inverse:
            return pyproj.transform(self.coord_proj,
                                    self.grid_proj,
                                    x, y)
        else:
            return pyproj.transform(self.grid_proj,
                                    self.coord_proj,
                                    x, y)

    def local2global(self, value, inverse=False):
        tpr = _cart2sph_np(value - self.grid_centre)
        if inverse:
            tpr -= [self.azimuth, self.dip, 0.0]
        else:
            tpr += [self.azimuth, self.dip, 0.0]
        return (_sph2cart_np(tpr) + self.grid_centre)

    def xyz2loc(self, value, inverse=False):
        if inverse:
            return self.local2global(self.grid_centre
                                     + (self.cell_size
                                        * (value - (self.cell_count - 1) / 2)))
        else:
            return ((self.local2global(value, inverse=True) - self.grid_centre)
                    / self.cell_size) + (self.cell_count - 1) / 2

    def index2loc(self, value, inverse=False):
        if inverse:
            return np.ravel_multi_index(value, self.cell_count, mode="clip",
                                        order=self.sort_order)
        else:
            out = np.vstack(np.unravel_index(value, self.cell_count,
                                             order=self.sort_order))
            return out.transpose()

    def xyz2index(self, value, inverse=False):
        if inverse:
            return self.xyz2loc(self.index2loc(value), inverse=True)
        else:
            return self.index2loc(self.xyz2loc(value), inverse=True)

    def xyz2coord(self, value, inverse=False):
        if inverse:
            x, y = self.xy2lonlat(value[:, 0], value[:, 1], inverse=True)
            z = value[:, 2]

            corners = self.grid_corners
            xmin, ymin, zmin = np.min(corners, axis=0)
            xmax, ymax, zmax = np.max(corners, axis=0)

            if x < xmin:
                x = np.array([xmin + self.cell_size[0] / 2])
            if x > xmax:
                x = np.array([xmax - self.cell_size[0] / 2])
            if y < ymin:
                y = np.array([ymin + self.cell_size[1] / 2])
            if y > ymax:
                y = np.array([ymax - self.cell_size[1] / 2])
            if z < zmin:
                z = np.array([zmin + self.cell_size[2] / 2])
            if z > zmax:
                z = np.array([zmax - self.cell_size[2] / 2])

            return np.array([x, y, z]).transpose()
        else:
            lon, lat = self.xy2lonlat(value[:, 0], value[:, 1])
            return np.array([lon, lat, value[:, 2]]).transpose()

    def coord2loc(self, value, inverse=False):
        if inverse:
            return self.xyz2coord(self.xyz2loc(value, inverse=True))
        else:
            return self.xyz2loc(self.xyz2coord(value, inverse=True))

    def coord2index(self, value, inverse=False):
        if inverse:
            return self.coord2loc(self.index2loc(value), inverse=True)
        else:
            return self.index2loc(self.coord2loc(value), inverse=True)

    @property
    def grid_origin(self):
        grid_size = (self.cell_count - 1) * self.cell_size
        return self.local2global(self.grid_centre - (grid_size / 2))

    @property
    def grid_corners(self):
        """
        Get the xyz positions of the cells on the edge of the grid

        """

        lc = self.cell_count - 1
        ly, lx, lz = np.meshgrid([0, lc[1]], [0, lc[0]], [0, lc[2]])
        loc = np.c_[lx.flatten(), ly.flatten(), lz.flatten()]
        return self.xyz2loc(loc, inverse=True)

    @property
    def grid_xyz(self):
        """
        Get the xyz positions of all of the cells in the grid

        """

        lc = self.cell_count
        ly, lx, lz = np.meshgrid(np.arange(lc[1]),
                                 np.arange(lc[0]),
                                 np.arange(lc[2]))
        loc = np.c_[lx.flatten(), ly.flatten(), lz.flatten()]
        coord = self.xyz2loc(loc, inverse=True)
        lx = coord[:, 0].reshape(lc)
        ly = coord[:, 1].reshape(lc)
        lz = coord[:, 2].reshape(lc)
        return lx, ly, lz


class NonLinLoc:
    """
    NonLinLoc class

    Reading and manipulating NLLoc Grids in a 2D or 3D format

    Attributes
    ----------

    Methods
    -------
    nlloc_load_file(filename)
        Parses information from .hdr and .buf files into NonLinLoc variables

    TO-DO
    -----
    Loading of 2D travel-times


    """

    def __init__(self):
        self.NLLoc_n = np.array([0, 0, 0])
        self.NLLoc_org = np.array([0, 0, 0])
        self.NLLoc_size = np.array([0, 0, 0])
        self.NLLoc_type = "TIME"
        self.NLLoc_proj = "NONE"
        # Has form lon - lat - rotation - reference ellipsoid - std1 - std2
        self.NLLoc_MapOrg = [0.0, 0.0, 0.0, "SIMPLE", 0.0, 0.0]
        self.NLLoc_data = None

    def nlloc_load_file(self, filename):
        """
        Parse information from .hdr and .buf files into NonLinLoc variables

        Parameters
        ----------
        filename : str
            File name (not including extension)

        """

        # Read the .hdr file
        f = open("{}.hdr".format(filename), "r")

        # Defining the grid dimensions
        params = f.readline().split()
        self.NLLoc_n = np.array([int(params[0]),
                                 int(params[1]),
                                 int(params[2])])
        self.NLLoc_org = np.array([float(params[3]),
                                   float(params[4]),
                                   float(params[5])])
        self.NLLoc_size = np.array([float(params[6]),
                                   float(params[7]),
                                   float(params[8])])
        self.NLLoc_type = params[9]

        # Defining the station information
        stats = f.readline().split()
        del stats

        # Defining the transform information
        trans = f.readline().split()
        if trans[1] == "NONE":
            self.NLLoc_proj = "NONE"
        if trans[1] == "SIMPLE":
            self.NLLoc_proj = "SIMPLE"
            self.NLLoc_MapOrg = [trans[5], trans[3], trans[7],
                                 "SIMPLE", "0.0", "0.0"]
        if trans[1] == "LAMBERT":
            self.NLLoc_proj = "LAMBERT"
            self.NLLoc_MapOrg = [trans[7], trans[5], trans[13],
                                 trans[3], trans[9], trans[11]]
        if trans[1] == "TRANS_MERC":
            self.NLLoc_proj = "TRANS_MERC"
            self.NLLoc_MapOrg = [trans[7], trans[5], trans[9],
                                 trans[3], "0.0", "0.0"]

        # Reading the .buf file
        fid = open("{}.buf".format(filename), "rb")
        data = struct.unpack("{}f".format(self.NLLoc_n[0]
                                          * self.NLLoc_n[1]
                                          * self.NLLoc_n[2]),
                             fid.read(self.NLLoc_n[0]
                                      * self.NLLoc_n[1]
                                      * self.NLLoc_n[2] * 4))
        self.NLLoc_data = np.array(data).reshape(self.NLLoc_n[0],
                                                 self.NLLoc_n[1],
                                                 self.NLLoc_n[2])

    def nlloc_project_grid(self):
        """
        Projecting the grid to the new coordinate system.

        This function also determines the 3D grid from the 2D grids from
        NonLinLoc

        """

        # Generating the correct NonLinLoc Formatted Grid
        if self.NLLoc_proj == "NONE":
            GRID_NLLOC = Grid3D(cell_count=self.NLLoc_n,
                                cell_size=self.NLLoc_size,
                                azimuth=0.0,
                                dip=0.0)
            GRID_NLLOC.nlloc_grid_centre(self.NLLoc_org[0], self.NLLoc_org[1])
        else:
            GRID_NLLOC = Grid3D(cell_count=self.NLLoc_n,
                                cell_size=self.NLLoc_size,
                                azimuth=self.NLLoc_MapOrg[2],
                                dip=0.0)
            GRID_NLLOC.lonlat_centre(self.NLLoc_MapOrg[0],
                                     self.NLLoc_MapOrg[1])

        # TO-DO: What is the text in NLLoc_MapOrg[3]?
        if self.NLLoc_proj == "LAMBERT":
            GRID_NLLOC.projections(grid_proj_type=self.NLLoc_MapOrg[3])

        if self.NLLoc_proj == "TRANS_MERC":
            GRID_NLLOC.projections(grid_proj_type=self.NLLoc_MapOrg[3])

        OrgX, OrgY, OrgZ = GRID_NLLOC.grid_xyz
        NewX, NewY, NewZ = self.grid_xyz

        self.NLLoc_data = griddata((OrgX.flatten(),
                                    OrgY.flatten(),
                                    OrgZ.flatten()),
                                   self.NLLoc_data.flatten(),
                                   (NewX, NewY, NewZ),
                                   method="nearest")

    def nlloc_regrid(self, decimate):
        """
        Redefining coordinate system to the file loaded

        Parameters
        ----------
        decimate :


        """

        centre = self.NLLoc_org + self.NLLoc_size * (self.NLLoc_n - 1) / 2
        self.centre = centre * [1000, 1000, -1000]
        self.elevation = self.NLLoc_org[2] * -1000
        self.cell_count = self.NLLoc_n
        self.cell_size = self.NLLoc_size * 1000
        self.dip = 0.0

        if self.NLLoc_proj == "NONE":
            self.azimuth = 0.0
            self.grid_centre = self.centre

        if self.NLLoc_proj == "SIMPLE":
            self.azimuth = self.NLLoc_MapOrg[2]
            self.nlloc_grid_centre(float(self.NLLoc_MapOrg[0]),
                                   float(self.NLLoc_MapOrg[1]))

        if self.NLLoc_proj == "LAMBERT":
            self.azimuth = float(self.NLLoc_MapOrg[2])
            self.nlloc_grid_centre(float(self.NLLoc_MapOrg[0]),
                                   float(self.NLLoc_MapOrg[1]))

        if self.NLLoc_proj == "TRANS_MERC":
            self.azimuth = float(self.NLLoc_MapOrg[2])
            self.nlloc_grid_centre(float(self.NLLoc_MapOrg[0]),
                                   float(self.NLLoc_MapOrg[1]))

        self.NLLoc_data = self.decimate_array(self.NLLoc_data,
                                              np.array(decimate))[:, :, ::-1]


class LUT(Grid3D, NonLinLoc):
    """
    Look-Up Table class

    Inherits from Grid3D and NonLinLoc classes

    Attributes
    ----------
    maps : dict
        Contains traveltime tables for P- and S-phases.

    Methods
    -------
    station_xyz(station=None)
        Returns the xyz position of a specific station relative to the origin
        (default returns all locations)
    decimate(ds, inplace=False)
        Downsample the initial velocity model tables that are loaded before
        processing


    TO-DO
    -----
    Weighting of the stations with distance (allow the user to define their own
    tables or define a fixed weighting for the problem)
    Move maps from being stored in RAM (use JSON or HDF5)


        _select_station - Selecting the stations to be used in the LUT
        set_station     - Defining the station locations to be used

    """

    def __init__(self, stations=None, cell_count=[51, 51, 31],
                 cell_size=[30.0, 30.0, 30.0], azimuth=0.0, dip=0.0):
        """
        Class initialisation method

        Parameters
        ----------
        stations : pandas DataFrame, optional
            Station information.
            Columns (in any order): ["Latitude", "Longitude", "Elevation",
                                     "Name"]

        cell_count : array-like
            Number of cells in each dimension of the grid

        cell_size : array-like
            Size of a cell in each dimension of the grid

        azimuth : float
            Angle between northing vertical plane and grid y-z plane

        dip : float
            Angle between horizontal plane and grid x-y plane

        """

        Grid3D.__init__(self, cell_count, cell_size, azimuth, dip)
        NonLinLoc.__init__(self)

        self.velocity_model = None
        self.station_data = stations
        self._maps = {}
        self.data = None

    def __str__(self):
        """
        Return short summary string of the LUT object

        It will provide the defining properties of the LUT - location, underlying
        grid properties and the simple 1D velocity structure.

        """
        out = "QuakeMigrate traveltime look-up table"
        out += "\n\tGrid centre\t:\t{lat:10.5f}\u00b0N {lon:10.5f}\u00b0E {dep:6.3f} km"
        out += "\n\tNumber of cells\t:\t{xn:4d} {yn:4d} {zn:4d}"
        out += "\n\tCell size\t:\t{xs:6.3f} km {ys:6.3f} km {zs:6.3f} km"
        out += "\n\t1D velocity model:"
        out += "\n\t\tDepth\tVp\tVs"

        out = out.format(xn=self.cell_count[0], yn=self.cell_count[1],
                         zn=self.cell_count[2], xs=self.cell_size[0] / 1000.,
                         ys=self.cell_size[1] / 1000., zs=self.cell_size[2] / 1000.,
                         lat=self.latitude, lon=self.longitude, dep=self.elevation / 1000.)

        # for i, layer in self.velocity_model.iterrows():
        #     vel = "\t\t{:6.3f}\t{:6.3f}\t{:6.3f}"
        #     vel = vel.format(layer["depth"] / 1000., layer["vp"] / 1000.,
        #                      layer["vs"] / 1000.)

        #     out += "\n{}".format(vel)

        return out

    def station_xyz(self, station=None):
        if station is None:
            stn = self.station_data
        else:
            station = self._select_station(station)
            stn = self.station_data[station]
        x, y = self.xy2lonlat(stn["Longitude"], stn["Latitude"], inverse=True)
        coord = np.c_[x, y, stn["Elevation"]]
        return coord

    def station_offset(self, station=None):
        coord = self.station_xyz(station)
        return coord - self.grid_centre

    @property
    def maps(self):
        """Get and set the traveltime tables"""
        return self._maps

    @maps.setter
    def maps(self, value):
        self._maps = value

    def _select_station(self, station_data):
        if self.station_data is None:
            return station_data

        nstn = len(self.station_data["Name"])
        flag = np.array(np.zeros(nstn, dtype=np.bool))
        for i, stn in enumerate(self.station_data["Name"]):
            if stn in station_data:
                flag[i] = True

        return flag

    def decimate(self, ds, inplace=False):
        """
        Up- or down-sample the travel-time tables by some factor

        Parameters
        ----------
        ds :

        inplace : bool
            Performs the operation to the travel-time table directly

        TO-DO
        -----
        I"m not sure that the inplace operation is doing the right thing? - CB


        """

        if not inplace:
            self = copy(self)
            self.maps = copy(self.maps)
        else:
            self = self

        ds = np.array(ds, dtype=np.int)
        cell_count = 1 + (self.cell_count - 1) // ds
        c1 = (self.cell_count - ds * (cell_count - 1) - 1) // 2
        cn = c1 + ds * (cell_count - 1) + 1
        centre_cell = (c1 + cn - 1) / 2
        centre = self.xyz2loc(centre_cell, inverse=True)
        self.cell_count = cell_count
        self.cell_size = self.cell_size * ds
        self.centre = centre

        maps = self.maps
        if maps is not None:
            for id_, map_ in maps.items():
                maps[id_] = np.ascontiguousarray(map_[c1[0]::ds[0],
                                                      c1[1]::ds[1],
                                                      c1[2]::ds[2], :])
        if not inplace:
            return self

    def decimate_array(self, data, ds):
        self = self
        ds = np.array(ds, dtype=np.int)
        cell_count = 1 + (self.cell_count - 1) // ds
        c1 = (self.cell_count - ds * (cell_count - 1) - 1) // 2
        cn = c1 + ds * (cell_count - 1) + 1
        centre_cell = (c1 + cn - 1) / 2
        centre = self.xyz2loc(centre_cell, inverse=True)
        self.cell_count = cell_count
        self.cell_size = self.cell_size * ds
        self.centre = centre

        array = np.ascontiguousarray(data[c1[0]::ds[0],
                                          c1[1]::ds[1],
                                          c1[2]::ds[2]])
        return array

    def get_values_at(self, loc, station=None):
        val = {}
        for map_ in self.maps.keys():
            val[map_] = self.get_value_at(map_, loc, station)
        return val

    def get_value_at(self, map_, loc, station=None):
        return self.interpolate(map_, loc, station)

    def value_at(self, map_, xyz, station=None):
        loc = self.xyz2loc(xyz)
        return self.interpolate(map_, loc, station)

    def values_at(self, xyz, station=None):
        loc = self.xyz2loc(xyz)
        return self.get_values_at(loc, station)

    def interpolator(self, map_, station=None):
        maps = self.fetch_map(map_, station)
        nc = self.cell_count
        cc = (np.arange(nc[0]), np.arange(nc[1]), np.arange(nc[2]))
        return RegularGridInterpolator(cc, maps, bounds_error=False)

    def interpolate(self, map_, loc, station=None):
        interp_fcn = self.interpolator(map_, station)
        return interp_fcn(loc)

    def fetch_map(self, map_, station=None):
        if station is None:
            return self.maps[map_]
        else:
            station = self._select_station(station)
            return self.maps[map_][..., station]

    def fetch_index(self, map_, sampling_rate, station=None):
        maps = self.fetch_map(map_, station)
        return np.rint(sampling_rate * maps).astype(np.int32)

    def compute_homogeneous_vmodel(self, vp, vs):
        """
        Calculate the travel-time tables for each station in a uniform velocity
        model

        Parameters
        ----------
        vp : float
            P-wave velocity (units: km / s)

        vs : float
            S-wave velocity (units: km / s)

        """

        rloc = self.station_xyz()
        nstn = rloc.shape[0]
        gx, gy, gz = self.grid_xyz
        ncell = self.cell_count
        p_map = np.zeros(np.r_[ncell, nstn])
        s_map = np.zeros(np.r_[ncell, nstn])
        for stn in range(nstn):
            dx = gx - float(rloc[stn, 0])
            dy = gy - float(rloc[stn, 1])
            dz = gz - float(rloc[stn, 2])
            dist = np.sqrt(dx**2 + dy**2 + dz**2)
            p_map[..., stn] = (dist / vp)
            s_map[..., stn] = (dist / vs)
        self.maps = {"TIME_P": p_map,
                     "TIME_S": s_map}

    def compute_1d_vmodel(self, p0, p1, gridspec, vmod_file, 
                          delimiter=",", nlloc_dx=0.1, nlloc_path="",
                          block_model=False):
        """
        Calculate 3D travel time lookup-tables from a 1D velocity model.

        NonLinLoc Grid2Time is used to generate a 2D lookup-table which is then
        swept across a 3D distance from station grid to populate a 3D travel
        time grid. The location of the stations should already have been added
        to the LUT using the function set_station().

        Parameters
        ----------
        p0 : dict
            Coordinate projection information

        p1 : dict
            Grid projection information

        gridspec : array-like
            Contains lon/lat of lower-left corner, lon/lat of upper-right
            corner, min/max grid depth and grid spacing (units: m)

        vmod_file : str
            File containing the velocity model to be used to generate the LUT.
            Contains columns with names "depth", "vp" and "vs"
            NOTE!! Exact column header names are required.

        delimiter : char, optional
            Delimiter for vmod_file: default = ","

        nlloc_dx : float, optional
            NLLoc 2D grid spacing (default: 0.1 km)

        nlloc_path : str
            Path to NonLinLoc binaries

        block_model : bool
            Interpret velocity model with constant velocity blocks

        """

        from subprocess import call, check_output, STDOUT

        vmodel = pd.read_csv(vmod_file, delimiter=",")
        self.velocity_model = vmodel

        p0_x0, p0_y0, p0_z0 = gridspec[0]
        p0_x1, p0_y1, p0_z1 = gridspec[1]
        dx, dy, dz = gridspec[2]

        # Define the projection
        self._coord_proj = p0
        self._grid_proj = p1

        # Define traveltime grid for QuakeMigrate
        p1_x0, p1_y0, p1_z0 = _coord_transform_np(p0, p1,
                                                  np.asarray([p0_x0,
                                                              p0_y0,
                                                              p0_z0]))
        p1_x1, p1_y1, p1_z1 = _coord_transform_np(p0, p1,
                                                  np.asarray([p0_x1,
                                                              p0_y1,
                                                              p0_z1]))

        # extract the number of nodes
        d_x = (p1_x1 - p1_x0)
        nx = int(np.ceil(d_x / dx)) + 1
        d_y = (p1_y1 - p1_y0)
        ny = int(np.ceil(d_y / dy)) + 1
        d_z = (p1_z1 - p1_z0)
        nz = int(np.ceil(d_z / dz)) + 1

        xvec = p1_x0 + (np.linspace(0, nx - 1, nx) * dx)
        yvec = p1_y0 + (np.linspace(0, ny - 1, ny) * dy)
        zvec = p1_z0 + (np.linspace(0, nz - 1, nz) * dz)

        X, Y, Z = np.meshgrid(xvec, yvec, zvec, indexing="ij")

        # make a folder structure to run nonlinloc in
        os.makedirs("time", exist_ok=True)
        os.makedirs("model", exist_ok=True)

        nstation = len(self.station_data["Name"])

        p_travel_times = np.empty((nx, ny, nz, nstation))
        s_travel_times = np.empty_like(p_travel_times)
        i = 0
        while i < nstation:
            p0_st_y = self.station_data["Latitude"][i]
            p0_st_x = self.station_data["Longitude"][i]
            p0_st_z = -self.station_data["Elevation"][i]
            name = self.station_data["Name"][i]

            print("Calculating travel-times for station", name)

            # get the maximum distance from station to corner of grid
            p1_st_loc = _coord_transform_np(p0, p1,
                                            np.asarray([p0_st_x,
                                                        p0_st_y,
                                                        p0_st_z]))
            p1_st_x, p1_st_y, p1_st_z = p1_st_loc

            # for nonlinloc the distances must be in km
            distance_grid = np.sqrt(np.square(X - p1_st_x) +
                                    np.square(Y - p1_st_y))
            distance_grid /= 1000.
            max_dist = np.max(distance_grid)

            # NLLOC needs the station to lie within the 2D section,
            # therefore we pick the depth extent of the 2D grid from
            # the maximum possible extent of the station and the grid
            min_z = np.min([p1_z0, p1_st_z])
            max_z = np.max([p1_z1, p1_st_z])
            depth_extent = np.asarray([min_z, max_z])

            for phase in ["P", "S"]:
                # Allow 2 nodes on depth extent as a computational buffer
                write_control_file(p1_st_x / 1000., p1_st_y / 1000.,
                                   p1_st_z / 1000., name,
                                   max_dist, self.velocity_model,
                                   depth_extent / 1000.,
                                   phase=phase, dx=nlloc_dx,
                                   block_model=block_model)

                print("\tRunning NonLinLoc phase =", phase)
                out = check_output([os.path.join(nlloc_path, "Vel2Grid"),
                                   "control.in"], stderr=STDOUT)
                if b"ERROR" in out:
                    raise Exception("Vel2Grid Error", out)

                out = check_output([os.path.join(nlloc_path, "Grid2Time"),
                                    "control.in"], stderr=STDOUT)
                if b"ERROR" in out:
                    raise Exception("Grid2Time Error", out)

                to_read = "./time/layer.{}.{}.time".format(phase, name)
                data, _, _, nll_gridspec = read_2d_nlloc(to_read)

                distance = distance_grid.flatten()
                depth = Z.flatten() / 1000.
                travel_time = bilinear_interp(np.vstack((distance, depth)).T,
                                              [nll_gridspec[0][1:],
                                               nll_gridspec[1][1:],
                                               nll_gridspec[2][1:]],
                                              data[0, :, :])

                travel_time = np.reshape(travel_time, (nx, ny, nz))
                if phase == "P":
                    p_travel_times[..., i] = travel_time
                elif phase == "S":
                    s_travel_times[..., i] = travel_time
                else:
                    raise Exception("HELP")

            i += 1

        # Define rest of the LUT parameters
        x = p1_x0 + dx * ((nx - 1) / 2.)
        y = p1_y0 + dy * ((ny - 1) / 2.)
        z = p1_z0 #+ dz * ((nz - 1) / 2.)
        self.cell_count = np.asarray([nx, ny, nz])
        self.cell_size = np.asarray([dx, dy, dz])
        self.longitude, self.latitude = self.xy2lonlat(x, y)
        self.elevation = -z
        self.azimuth = 0.0
        self.dip = 0.0
        self._update_grid_centre()

        # Flip TT maps so the indexing is consistent (z ordered from deepest to shallowest)
        p_travel_times = p_travel_times[..., ::-1, :]
        s_travel_times = s_travel_times[..., ::-1, :]

        self.maps = {"TIME_P": p_travel_times, "TIME_S": s_travel_times}

        call(["rm", "-rf", "control.in", "time", "model"])

    def compute_1d_vmodel_skfmm(self, vmod_file, header=False, delimiter=","):
        """
        Calculate the travel-time tables for each station in a velocity model
        that varies with depth

        Parameters
        ----------
        vmod_file : str
            File containing the velocity model to be used to generate the LUT.
            Columns: ["Z", "Vp", "Vs"]
                Z : Depth of each layer in model (positive up; units: metres)
                Vp : P-wave velocity for each layer in model (units: km / s)
                Vs : S-wave velocity for each layer in model (units: km / s)

        header : bool, optional
            Does the vmod_file supplied have a header line? If so set header
            to True. Default: False

        """

        if header:
            vmod = pd.read_csv(vmod_file, delimiter=delimiter).values
        else:
            vmod = pd.read_csv(vmod_file, header=None, delimiter=delimiter).values
        z, vp, vs = vmod[:, 0], vmod[:, 1] * 1000, vmod[:, 2] * 1000

        rloc = self.station_xyz()
        nstn = rloc.shape[0]
        ix, iy, iz = self.grid_xyz
        p_map = np.zeros(ix.shape + (rloc.shape[0],))
        s_map = np.zeros(ix.shape + (rloc.shape[0],))

        z = np.insert(np.append(z, np.finfo(float).min), 0, np.finfo(float).max)
        vp = np.insert(np.append(vp, vp[-1]), 0, vp[0])
        vs = np.insert(np.append(vs, vs[-1]), 0, vs[0])

        f = interp1d(z, vp)
        gvp = f(iz)
        f = interp1d(z, vs)
        gvs = f(iz)

        for stn in range(nstn):
            msg = "Generating 1D Travel-Time Table - {} of {}"
            msg = msg.format(stn + 1, nstn)
            print(msg)

            p_map[..., stn] = eikonal(ix, iy, iz,
                                      self.cell_size[0],
                                      self.cell_size[1],
                                      self.cell_size[2],
                                      gvp, rloc[stn][np.newaxis, :])
            s_map[..., stn] = eikonal(ix, iy, iz,
                                      self.cell_size[0],
                                      self.cell_size[1],
                                      self.cell_size[2],
                                      gvs, rloc[stn][np.newaxis, :])

        self.maps = {"TIME_P": p_map,
                     "TIME_S": s_map}

    def compute_3d_vmodel(self, path):
        """

        """
        raise NotImplementedError

    def read_3d_nlloc_lut(self, path, regrid=True, decimate=[1, 1, 1]):
        """
        Calculate the travel-time tables for each station in a velocity model
        that varies over all dimensions.

        This velocity model comes from a NonLinLoc velocity model file.

        Parameters
        ----------
        path : str
            Location of .buf and .hdr files

        regrid : bool, optional
            Currently this has to be set to True for this function to work.
            *** TO BE FIXED ***

        Raises
        ------
        MemoryError
            If travel-time grids size exceeds available memory

        """

        nstn = len(self.station_data["Name"])
        for st in range(nstn):
            name = self.station_data["Name"][st]
            msg = "Loading P- and S- traveltime maps for {}"
            msg = msg.format(name)
            print(msg)

            # Reading in P-wave
            self.nlloc_load_file("{}.P.{}.time".format(path, name))
            if not regrid:
                self.nlloc_project_grid()
            else:
                self.nlloc_regrid(decimate)

            if ("p_map" not in locals()) and ("s_map" not in locals()):
                ncell = self.NLLoc_data.shape
                try:
                    p_map = np.zeros(np.r_[ncell, nstn])
                    s_map = np.zeros(np.r_[ncell, nstn])
                except MemoryError:
                    msg = "P- and S-traveltime maps exceed available memory."
                    raise MemoryError(msg)

            p_map[..., st] = self.NLLoc_data

            self.nlloc_load_file("{}.S.{}.time".format(path, name))
            if not regrid:
                self.nlloc_project_grid()
            else:
                self.nlloc_regrid(decimate)

            s_map[..., st] = self.NLLoc_data

        self.maps = {"TIME_P": p_map,
                     "TIME_S": s_map}

    def save(self, filename):
        """
        Create a pickle file containing the look-up table

        Parameters
        ----------
        filename : str
            Path to location to save pickle file

        """

        with open(filename, "wb") as f:
            pickle.dump(self.__dict__, f, 2)

    def load(self, filename):
        """
        Read the contents of a pickle file to __dict__

        Parameters
        ----------
        filename : str
            Path to pickle file to load

        """

        with open(filename, "rb") as f:
            tmp_dict = pickle.load(f)

        self.__dict__.update(tmp_dict)

    def plot_3d(self, map_, station, output_file=None):
        """
        Creates a 3-dimensional representation of the station locations with
        optional velocity model if specified.

        Parameters
        ----------
        map_ : str
            Specifies which velocity model to plot
        station : str

        output_file : str, optional
            Location to save file to
        """
        raise NotImplementedError
