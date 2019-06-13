# -*- coding: utf-8 -*-
"""
Module to perform QuakeMigrate detect, trigger and locate

"""

import os
import time
import warnings

from obspy import UTCDateTime
from obspy.signal.trigger import classic_sta_lta
from obspy.signal.invsim import cosine_taper
import pandas as pd
from scipy.signal import butter, lfilter, fftconvolve
from scipy.optimize import curve_fit
from scipy.interpolate import Rbf
import numpy as np
import matplotlib
try:
    os.environ["DISPLAY"]
    matplotlib.use("Qt5Agg")
except KeyError:
    matplotlib.use("Agg")
import matplotlib.pylab as plt

import QMigrate.core.model as cmod
import QMigrate.core.QMigratelib as ilib
import QMigrate.io.quakeio as qio
import QMigrate.plot.quakeplot as qplot

# Catch warnings as errors
warnings.filterwarnings("always")

def TicTocGenerator():
    # Generator that returns time differences
    ti = 0  # initial time
    tf = time.time()  # final time
    while True:
        ti = tf
        tf = time.time()
        yield tf - ti  # returns the time difference


TicToc = TicTocGenerator()  # create an instance of the TicTocGen generator


# This will be the main function through which we define both tic() and toc()
def toc(tmp_bool=True):
    # Prints the time difference yielded by generator instance TicToc
    tmp_time_interval = next(TicToc)
    if tmp_bool:
        print("    \tElapsed time: {:.6f} seconds.\n".format(tmp_time_interval))


def tic():
    # Records a time in TicToc, marks the beginning of a time interval
    toc(False)


def gaussian_1d(x, a, b, c):
    """
    Create a 1-dimensional Gaussian function

    Parameters
    ----------
    x : array-like
        array of x values

    a : float / int
        amplitude (height of gaussian)

    b : float / int
        mean (centre of gaussian)

    c : float / int
        sigma (width of gaussian)

    Returns
    -------
    f : function
        one-dimentional gaussian function

    """

    f = a * np.exp(-1. * ((x - b) ** 2) / (2 * (c ** 2)))
    return f


def gaussian_3d(nx, ny, nz, sgm, K):
    """
    Create a 3-dimensional Gaussian function

    Parameters
    ----------
    nx : array-like
         array of x values

    ny : array-like
         array of y values

    nz : array-like
         array of z values

    sgm : float / int
          sigma (width of gaussian in all directions)

    Returns
    -------
    f : function
        three-dimensional gaussian function

    """

    nx2 = (nx - 1) / 2
    ny2 = (ny - 1) / 2
    nz2 = (nz - 1) / 2
    x = np.linspace(-nx2, nx2, nx)
    y = np.linspace(-ny2, ny2, ny)
    z = np.linspace(-nz2, nz2, nz)
    ix, iy, iz = np.meshgrid(x, y, z, indexing="ij")

    if np.isscalar(sgm):
        sgm = np.repeat(sgm, 3)
    sx, sy, sz = sgm

    f = np.exp(- (ix * ix) / (2 * sx * sx)
                  - (iy * iy) / (2 * sy * sy)
                  - (iz * iz) / (2 * sz * sz)) + K
    return f

def sta_lta_centred(a, nsta, nlta):
    """
    Calculates the ratio of the average signal of a short-term window to a
    long-term window.

    Parameters
    ----------
    a : array-like
        Signal array
    nsta : int
        Number of samples in short-term window
    nlta : int
        Number of samples in long-term window

    """

    nsta = int(nsta)
    nlta = int(nlta)

    # Cumulative sum to calculate moving average
    sta = np.cumsum(a ** 2)
    sta = np.require(sta, dtype=np.float)
    lta = sta.copy()

    # Compute the STA and the LTA
    sta[nsta:] = sta[nsta:] - sta[:-nsta]
    sta[nsta:-nsta] = sta[nsta*2:]
    sta /= nsta

    lta[nlta:] = lta[nlta:] - lta[:-nlta]
    lta /= nlta

    sta[:(nlta - 1)] = 0
    sta[-nsta:] = 0

    # Avoid division by zero by setting zero values to tiny float
    dtiny = np.finfo(0.0).tiny
    idx = lta < dtiny
    lta[idx] - dtiny

    return sta / lta


def onset(sig, stw, ltw, centred=False):
    """
    Define an onset function

    Parameters
    ----------
    sig :

    stw :

    ltw :

    Returns
    -------

    """

    n_channels, n_samples = sig.shape
    onset = np.copy(sig)
    onset_raw = np.copy(sig)
    for i in range(n_channels):
        if np.sum(sig[i, :]) == 0.0:
            onset[i, :] = 0.0
            onset_raw[i, :] = onset[i, :]
        else:
            if centred is True:
                onset[i, :] = sta_lta_centred(sig[i, :], stw, ltw)
            else:
                onset[i, :] = classic_sta_lta(sig[i, :], stw, ltw)
            onset_raw[i, :] = onset[i, :]
            np.clip(1 + onset[i, :], 0.8, np.inf, onset[i, :])
            np.log(onset[i, :], onset[i, :])

    return onset_raw, onset


def filter(sig, srate, lc, hc, order=3):
    """

    """

    b1, a1 = butter(order, [2.0*lc/srate, 2.0*hc/srate], btype="band")
    nchan, nsamp = sig.shape
    fsig = np.copy(sig)
    # sig = detrend(sig)
    for ch in range(0, nchan):
        fsig[ch, :] = fsig[ch, :] - fsig[ch, 0]
        tap = cosine_taper(len(fsig[ch, :]), 0.1)
        fsig[ch, :] = fsig[ch, :] * tap
        fsig[ch, :] = lfilter(b1, a1, fsig[ch, ::-1])[::-1]
        fsig[ch, :] = lfilter(b1, a1, fsig[ch, :])

    return fsig


class DefaultSeisScan(object):
    """
    Contains default parameter information for SeisScan

    """

    def __init__(self):
        self.lookup_table = None
        self.seis_reader = None
        self.p_bp_filter = [2.0, 16.0, 3]
        self.s_bp_filter = [2.0, 12.0, 3]
        self.p_onset_win = [0.2, 1.0]
        self.s_onset_win = [0.2, 1.0]
        self.detection_threshold = 4.0
        self.time_step = 10
        self.decimate = [1, 1, 1]
        self.sampling_rate = 1000.0
        self.set_onset_centred = None

        self.pick_threshold = 1.0

        self.marginal_window = 30
        self.minimum_repeat = 30
        self.percent_tt = 0.1
        self.picking_mode = "Gaussian"
        self.location_error = 0.95
        self.normalise_coalescence = False
        self.deep_learning = False
        self.output_sampling_rate = None

        self.pre_pad = None
        self.time_step = 10.0
        self.n_cores = 1

        # Plotting functionality
        self.plot_coal_grid = False
        self.plot_coal_video = False
        self.plot_coal_picture = False
        self.plot_coal_trace = False

        self.xy_files = None


class SeisScan(DefaultSeisScan):
    """
    QuakeMigrate scanning class

    Forms the core of the QuakeMigrate method, providing wrapping functions for
    the C-compiled methods.

    Attributes
    ----------
    pre_pad : float

    post_pad : float
        Maximum travel-time from a point in the grid to a station

    Methods
    -------
    detect(start_time, end_time, log=False)
        Core detection method
    trigger(start_time, end_time)
        Core trigger method
    """

    raw_data = {}
    filt_data = {}
    onset_data = {}

    DEFAULT_GAUSSIAN_FIT = {"popt": 0,
                            "xdata": 0,
                            "xdata_dt": 0,
                            "PickValue": -1}

    EVENT_FILE_COLS = ["DT", "COA", "X", "Y", "Z",
                       "LocalGaussian_X", "LocalGaussian_Y", "LocalGaussian_Z",
                       "LocalGaussian_ErrX", "LocalGaussian_ErrY",
                       "LocalGaussian_ErrZ", "GlobalCovariance_X",
                       "GlobalCovariance_Y", "GlobalCovariance_Z",
                       "GlobalCovariance_ErrX", "GlobalCovariance_ErrY",
                       "GlobalCovariance_ErrZ"]

    def __init__(self, data, lookup_table, reader=None, params=None,
                 output_path=None, output_name=None):
        """
        Class initialisation method

        Parameters
        ----------
        data :

        lut :

        reader :

        params :

        output_path :

        output_name :


        """

        DefaultSeisScan.__init__(self)

        self.data = data
        lut = cmod.LUT()
        lut.load(lookup_table)
        self.lut = lut
        self.seis_reader = reader

        if output_path is not None:
            self.output = qio.QuakeIO(output_path, output_name)
        else:
            self.output = None

        ttmax = np.max(lut.fetch_map("TIME_S"))
        self.post_pad = round(ttmax + ttmax*0.05)

        # Internal variables
        if self.set_onset_centred is None:
            self._onset_centred = False
        elif self.set_onset_centred is True:
            self._onset_centred = True
        elif not self.set_onset_centred:
            self._onset_centred = False
        else:
            msg = 'set_onset_centre must be either True or False !'
            raise Exception(msg)

        msg = "=" * 126 + "\n"
        msg += "=" * 126 + "\n"
        msg += "   QuakeMigrate - Coalescence Scanning - Path: {} - Name: {}\n"
        msg += "=" * 126 + "\n"
        msg += "=" * 126 + "\n"
        msg += "\n"
        msg = msg.format(self.output.path, self.output.name)
        print(msg)

    def __str__(self):
        """
        Return short summary string of the SeisScan object

        It will provide information on all of the various parameters that the
        user can/has set.

        """

        out = "QuakeMigrate parameters"
        out += "\n\tTime step\t\t:\t{}".format(self.time_step)
        out += "\n\n\tData sampling rate\t:\t{}".format(self.sampling_rate)
        out += "\n\tOutput sampling rate\t:\t{}".format(
            self.output_sampling_rate)
        out += "\n\n\tDecimation\t\t:\t[{}, {}, {}]".format(
            self.decimate[0], self.decimate[1], self.decimate[2])
        out += "\n\n\tBandpass filter P\t:\t[{}, {}, {}]".format(
            self.p_bp_filter[0], self.p_bp_filter[1], self.p_bp_filter[2])
        out += "\n\tBandpass filter S\t:\t[{}, {}, {}]".format(
            self.s_bp_filter[0], self.s_bp_filter[1], self.s_bp_filter[2])
        out += "\n\n\tOnset P [STA, LTA]\t:\t[{}, {}]".format(
            self.p_onset_win[0], self.p_onset_win[1])
        out += "\n\tOnset S [STA, LTA]\t:\t[{}, {}]".format(
            self.s_onset_win[0], self.s_onset_win[1])
        out += "\n\n\tPre-pad\t\t\t:\t{}".format(self.pre_pad)
        out += "\n\tPost-pad\t\t:\t{}".format(self.post_pad)
        out += "\n\n\tMarginal window\t\t:\t{}".format(self.marginal_window)
        out += "\n\tMinimum repeat\t\t:\t{}".format(self.minimum_repeat)
        out += "\n\tDetection threshold\t:\t{}".format(self.detection_threshold)
        out += "\n\tPick threshold\t\t:\t{}".format(self.pick_threshold)
        out += "\n\tPicking mode\t\t:\t{}".format(self.picking_mode)
        out += "\n\tPercent ttime\t\t:\t{}".format(self.percent_tt)
        out += "\n\tLocation error\t\t:\t{}".format(self.location_error)
        out += "\n\n\tCentred onset\t\t:\t{}".format(self._onset_centred)
        out += "\n\tNormalise coalescence\t:\t{}".format(
            self.normalise_coalescence)
        out += "\n\n\tNumber of CPUs\t\t:\t{}".format(self.n_cores)

        return out

    def detect(self, start_time, end_time, log=False):
        """
        Searches through continuous data to find earthquakes

        Parameters
        ----------
        start_time : str

        end_time : str

        log : bool, optional
            Output processing to a log file

        """

        # Convert times to UTCDateTime objects
        start_time = UTCDateTime(start_time)
        end_time = UTCDateTime(end_time)

        self.log = log

        # Conduct the continuous compute on the decimated grid
        self.lut = self.lut.decimate(self.decimate)

        # Define pre-pad as a function of the onset windows
        if self.pre_pad is None:
            self.pre_pad = max(self.p_onset_win[1],
                               self.s_onset_win[1]) \
                           + 3 * max(self.p_onset_win[0],
                                     self.s_onset_win[0])

        # Detect the possible events from the decimated grid
        self._continuous_compute(start_time, end_time)

    def trigger(self, start_time, end_time, savefig=True, log=False):
        """

        Parameters
        ----------
        start_time : str
            Start time to perform trigger from
        end_time : str
            End time to perform trigger to
        savefig : bool, optional
            Saves plots if True
        log : bool, optional
            Output processing to a log file

        """

        # Convert times to UTCDateTime objects
        start_time = UTCDateTime(start_time)
        end_time = UTCDateTime(end_time)

        self.log = log

        msg = "=" * 126 + "\n"
        msg += "   TRIGGER - Triggering events from coalescence\n"
        msg += "=" * 126 + "\n"
        msg += "\n"
        msg += "   Parameters specified:\n"
        msg += "         Start time                = {}\n"
        msg += "         End   time                = {}\n"
        msg += "         Number of CPUs            = {}\n"
        msg += "\n"
        msg += "         Marginal window           = {} s\n"
        msg += "         Minimum repeat            = {} s\n\n"
        msg += "=" * 126 + "\n"
        msg = msg.format(str(start_time), str(end_time), self.n_cores,
                         self.marginal_window, self.minimum_repeat)

        if self.log:
            self.output.write_log(msg)
        else:
            print(msg)

        if self.minimum_repeat < self.marginal_window:
            msg = "Minimum repeat must be greater than or equal to marginal window."
            raise Exception(msg)

        # Intial detection of the events from .scn file
        coa_val = self.output.read_decscan()
        events = self._trigger_scn(coa_val, start_time, end_time)

        if events is None:
            print("No events above the threshold. Reduce the threshold value")
        else:
            self.output.write_triggered_events(events)

        self.plot_scn(events=events, start_time=start_time,
                      end_time=end_time, stations=self.lut.station_data,
                      savefig=savefig)

    def locate(self, start_time, end_time, cut_mseed=False, log=False):
        """

        Parameters
        ----------
        start_time : str
            Start time to perform trigger from
        end_time : str
            End time to perform trigger to
        cut_mseed : bool, optional
            Saves cut mSEED files if True
        log : bool, optional
            Output processing to a log file

        """

        # Convert times to UTCDateTime objects
        start_time = UTCDateTime(start_time)
        end_time = UTCDateTime(end_time)

        self.log = log

        msg = "=" * 126 + "\n"
        msg += "   LOCATE - Determining earthquake location and error\n"
        msg += "=" * 126 + "\n"
        msg += "\n"
        msg += "   Parameters specified:\n"
        msg += "         Start time                = {}\n"
        msg += "         End   time                = {}\n"
        msg += "         Number of CPUs            = {}\n\n"
        msg += "=" * 126 + "\n"
        msg = msg.format(str(start_time), str(end_time), self.n_cores)
        if self.log:
            self.output.write_log(msg)
        else:
            print(msg)

        events = self.output.read_triggered_events(start_time, end_time)

        if self.set_onset_centred is None:
            self._onset_centred = True
        elif self.set_onset_centred is True:
            self._onset_centred = True
        elif self.set_onset_centred is False:
            self._onset_centred = False
        else:
            msg = "set_onset_centre must be either True or False!"
            raise Exception(msg)

        n_evts = len(events)

        # Conduct the continuous compute on the decimated grid
        self.lut = self.lut.decimate(self.decimate)

        if self.pre_pad is None:
            self.pre_pad = max(self.p_onset_win[1],
                               self.s_onset_win[1]) \
                           + 3 * max(self.p_onset_win[0],
                                     self.s_onset_win[0])

        # Adjust pre- and post-pad to take into account cosine taper
        t_length = self.pre_pad + 4*self.marginal_window + self.post_pad
        self.pre_pad += round(t_length * 0.06)
        self.post_pad += round(t_length * 0.06)

        for i, event in events.iterrows():
            evt_id = event["EventID"]
            msg = "=" * 126 + "\n"
            msg += "    EVENT - {} of {} - {}\n"
            msg += "=" * 126 + "\n"
            msg += "    Determining event location..."
            msg = msg.format(i + 1, n_evts, evt_id)
            if self.log:
                self.output.write_log(msg)
            else:
                print(msg)

            tic()
            print("    Computing 4D coalescence grid")

            w_beg = event["CoaTime"] - 2*self.marginal_window - self.pre_pad
            w_end = event["CoaTime"] + 2*self.marginal_window + self.post_pad

            self.data.read_mseed(w_beg, w_end, self.sampling_rate)
            daten, dsnr, dsnr_norm, dloc, map_ = self._compute(
                                                    w_beg, w_end,
                                                    self.data.signal,
                                                    self.data.availability)
            dcoord = self.lut.xyz2coord(np.array(dloc).astype(int))

            event_coa_val = pd.DataFrame(np.array((daten, dsnr,
                                                   dcoord[:, 0],
                                                   dcoord[:, 1],
                                                   dcoord[:, 2])).transpose(),
                                         columns=["DT", "COA", "X", "Y", "Z"])
            event_coa_val["DT"] = event_coa_val["DT"].apply(UTCDateTime)
            event_coa_val_dtmax = event_coa_val["DT"].iloc[event_coa_val["COA"].astype("float").idxmax()]
            w_beg_mw = event_coa_val_dtmax - self.marginal_window
            w_end_mw = event_coa_val_dtmax + self.marginal_window

            if (event_coa_val_dtmax >= event["CoaTime"] - self.marginal_window) \
               and (event_coa_val_dtmax <= event["CoaTime"] + self.marginal_window):
                w_beg_mw = event_coa_val_dtmax - self.marginal_window
                w_end_mw = event_coa_val_dtmax + self.marginal_window
            else:
                msg = "----- Event {} is outside marginal window.\n"
                msg += "----- Define more realistic error."
                msg = msg.format(evt_id)
                if self.log:
                    self.output.write_log(msg)
                else:
                    print(msg)
                continue


            event = event_coa_val
            event = event[(event["DT"] >= w_beg_mw) & (event["DT"] <= w_end_mw)]
            map_ = map_[:, :, :, event.index[0]:event.index[-1]]
            event = event.reset_index(drop=True)
            event_max = event.iloc[event["COA"].astype("float").idxmax()]

            # Determining the hypocentral location from the maximum over
            # the marginal window.
            picks, GAUP, GAUS = self._arrival_trigger(event_max, evt_id)

            station_pick = {}
            station_pick["Pick"] = picks
            station_pick["GAU_P"] = GAUP
            station_pick["GAU_S"] = GAUS
            toc()

            # Determining earthquake location error
            tic()
            print("    Determining earthquake location and uncertainty")
            loc_spline, loc, loc_err, loc_cov, loc_err_cov = self._location_error(map_)
            toc()

            evt = pd.DataFrame([[event_max.values[0],
                                           event_max.values[1],
                                           loc_spline[0],loc_spline[1],loc_spline[2],
                                           loc[0], loc[1], loc[2],
                                           loc_err[0], loc_err[1], loc_err[2],
                                           loc_cov[0], loc_cov[1], loc_cov[2],
                                           loc_err_cov[0], loc_err_cov[1],
                                           loc_err_cov[2]]],
                               columns=self.EVENT_FILE_COLS)
            self.output.write_event(evt, evt_id)

            if cut_mseed:
                print("    Creating cut Mini-SEED")
                tic()
                self.output.cut_mseed(self.data, evt_id)
                toc()

            # Outputting coalescence grids and triggered events
            if self.plot_coal_trace:
                tic()
                print("    Creating station traces...")
                seis_plot = qplot.QuakePlot(self.lut,
                                            map_,
                                            self.coa_map,
                                            self.data,
                                            event,
                                            station_pick,
                                            self.marginal_window)
                out = str(self.output.run / "traces" / "{}_{}".format(
                    self.output.name,
                    evt_id))
                seis_plot.coalescence_trace(output_file=out)
                del seis_plot
                toc()

            if self.plot_coal_grid:
                tic()
                print("    Creating 4D coalescence grids...")
                self.output.write_coal4D(map_, evt_id, w_beg, w_end)
                toc()

            if self.plot_coal_video:
                tic()
                print("    Creating seismic videos...")
                seis_plot = qplot.QuakePlot(self.lut,
                                            map_,
                                            self.coa_map,
                                            self.data,
                                            event,
                                            station_pick,
                                            self.marginal_window)
                out = str(self.output.run / "videos" / "{}_{}".format(
                    self.output.name,
                    evt_id))
                seis_plot.coalescence_video(output_file=out)
                del seis_plot
                toc()

            if self.plot_coal_picture:
                tic()
                print("    Creating overview figure...")
                seis_plot = qplot.QuakePlot(self.lut,
                                            map_,
                                            self.coa_map,
                                            self.data,
                                            event,
                                            station_pick,
                                            self.marginal_window)
                out = str(self.output.run / "summaries" / "{}_{}".format(
                    self.output.name,
                    evt_id))
                seis_plot.coalescence_marginal(output_file=out,
                                               earthquake=evt)
                del seis_plot
                toc()

            print("=" * 126 + "\n")

            del map_, event, station_pick
            self.coa_map = None

    def plot_scn(self, events, start_time, end_time, stations=None, savefig=False):
        """
        Plots the data from a .scnmseed file

        Parameters
        ----------
        events :

        start_time : UTCDateTime

        end_time : UTCDateTime

        stations :

        savefig : bool, optional
            Output the plot as a file. The plot is just shown by default.

        TO-DO
        -----
        Plot station availability if requested.

        """

        fname = (self.output.run / self.output.name).with_suffix(".scnmseed")

        if fname.exists():
            # Loading the .scn file
            data = self.output.read_decscan()
            data["DT"] = pd.to_datetime(data["DT"].astype(str))

            fig = plt.figure(figsize=(30, 15))
            fig.patch.set_facecolor("white")
            coa = plt.subplot2grid((6, 16), (0, 0), colspan=9, rowspan=3)
            coa_norm = plt.subplot2grid((6, 16), (3, 0), colspan=9, rowspan=3,
                                        sharex=coa)
            xy = plt.subplot2grid((6, 16), (0, 10), colspan=4, rowspan=4)
            xz = plt.subplot2grid((6, 16), (4, 10), colspan=4, rowspan=2,
                                  sharex=xy)
            yz = plt.subplot2grid((6, 16), (0, 14), colspan=2, rowspan=4,
                                  sharey=xy)

            coa.plot(data["DT"], data["COA"], color="blue", zorder=10,
                     label="Maximum coalescence", linewidth=0.5)
            coa.get_xaxis().set_ticks([])
            coa_norm.plot(data["DT"], data["COA_N"], color="blue", zorder=10,
                          label="Maximum coalescence", linewidth=0.5)

            if events is not None:
                for i, event in events.iterrows():
                    if i == 0:
                        label1 = "Minimum repeat window"
                        label2 = "Marginal window"
                        label3 = "Detected events"
                    else:
                        label1 = ""
                        label2 = ""
                        label3 = ""

                    for plot in [coa, coa_norm]:
                        plot.axvspan((event["MinTime"]).datetime,
                                     (event["MaxTime"]).datetime,
                                     label=label1, alpha=0.5, color="red")
                        plot.axvline((event["CoaTime"] - self.marginal_window).datetime,
                                     label=label2, c="m", linestyle="--", linewidth=1.75)
                        plot.axvline((event["CoaTime"] + self.marginal_window).datetime,
                                     c="m", linestyle="--", linewidth=1.75)
                        plot.axvline(event["CoaTime"].datetime, label=label3,
                                     c="m", linewidth=1.75)

            props = {"boxstyle": "round",
                     "facecolor": "white",
                     "alpha": 0.5}
            coa.set_xlim(start_time.datetime, end_time.datetime)
            coa.text(.5, .9, "Maximum coalescence",
                     horizontalalignment="center",
                     transform=coa.transAxes, bbox=props)
            coa.legend(loc=2)
            coa.set_ylabel("Maximum coalescence value")
            coa_norm.set_xlim(start_time.datetime, end_time.datetime)
            coa_norm.text(.5, .9, "Normalised maximum coalescence",
                          horizontalalignment="center",
                          transform=coa_norm.transAxes, bbox=props)
            coa_norm.legend(loc=2)
            coa_norm.set_ylabel("Normalised maximum coalescence value")
            coa_norm.set_xlabel("DateTime")

            if events is not None:
                if self.normalise_coalescence:
                    coa_norm.axhline(self.detection_threshold, c="g",
                                     label="Detection threshold")
                else:
                    coa_norm.axhline(self.detection_threshold, c="g",
                                     label="Detection threshold")

                # Plotting the scatter of the earthquake locations
                xy.scatter(events["COA_X"], events["COA_Y"], 50, events["COA_V"])
                yz.scatter(events["COA_Z"], events["COA_Y"], 50, events["COA_V"])
                xz.scatter(events["COA_X"], events["COA_Z"], 50, events["COA_V"])

            xy.set_title("Decimated coalescence earthquake locations")
            xy.get_xaxis().set_ticks([])
            xy.get_yaxis().set_ticks([])

            yz.yaxis.tick_right()
            yz.yaxis.set_label_position("right")
            yz.set_ylabel("Latitude (deg)")
            yz.set_xlabel("Depth (m)")

            xz.yaxis.tick_right()
            xz.invert_yaxis()
            xz.yaxis.set_label_position("right")
            xz.set_xlabel("Longitude (deg)")
            xz.set_ylabel("Depth (m)")

            if stations is not None:
                xy.scatter(stations["Longitude"], stations["Latitude"], 20,)
                xy.scatter(stations["Longitude"], stations["Latitude"], 15,
                           marker="^", color="black")
                xz.scatter(stations["Longitude"], stations["Elevation"], 15,
                           marker="^", color="black")
                yz.scatter(stations["Elevation"], stations["Latitude"], 15,
                           marker="<", color="black")
                for i, txt in enumerate(stations["Name"]):
                    xy.annotate(txt, [stations["Longitude"][i],
                                stations["Latitude"][i]], color="black")

            # Saving figure if defined
            if savefig:
                out = self.output.run / "{}_Trigger".format(self.output.name)
                out = str(out.with_suffix(".pdf"))
                plt.savefig(out)
            else:
                plt.show()

        else:
            msg = "Please run detect to generate a .scnmseed file."
            print(msg)

    def _continuous_compute(self, start_time, end_time):
        """


        Parameters
        ----------
        start_time :

        end_time :

        """

        # # Clear existing .scn files
        # self.output.del_scan()

        coalescence_mSEED = None

        msg = "=" * 126 + "\n"
        msg += "   DETECT - Continuous Seismic Processing\n"
        msg += "=" * 126 + "\n"
        msg += "\n"
        msg += "   Parameters specified:\n"
        msg += "         Start time                = {}\n"
        msg += "         End   time                = {}\n"
        msg += "         Time step (s)             = {}\n"
        msg += "         Number of CPUs            = {}\n"
        msg += "\n"
        msg += "         Sampling rate             = {}\n"
        msg += "         Grid decimation [X, Y, Z] = [{}, {}, {}]\n"
        msg += "         Bandpass filter P         = [{}, {}, {}]\n"
        msg += "         Bandpass filter S         = [{}, {}, {}]\n"
        msg += "         Onset P [STA, LTA]        = [{}, {}]\n"
        msg += "         Onset S [STA, LTA]        = [{}, {}]\n"
        msg += "\n"
        msg += "=" * 126
        msg = msg.format(str(start_time), str(end_time), self.time_step,
                         self.n_cores, self.sampling_rate,
                         self.decimate[0], self.decimate[1], self.decimate[2],
                         self.p_bp_filter[0], self.p_bp_filter[1],
                         self.p_bp_filter[2], self.s_bp_filter[0],
                         self.s_bp_filter[1], self.s_bp_filter[2],
                         self.p_onset_win[0], self.p_onset_win[1],
                         self.s_onset_win[0], self.s_onset_win[1])
        if self.log:
            self.output.write_log(msg)
        else:
            print(msg)

        t_length = self.pre_pad + self.post_pad + self.time_step
        self.pre_pad += round(t_length * 0.06)
        self.post_pad += round(t_length * 0.06)

        try:
            nsteps = int(np.ceil((end_time - start_time) / self.time_step))
        except AttributeError:
            msg = "Time step has not been specified"
            print(msg)

        for i in range(nsteps):
            w_beg = start_time + self.time_step * i - self.pre_pad
            w_end = start_time + self.time_step * (i + 1) + self.post_pad

            msg = ("~" * 27) + " Processing : {} - {} " + ("~" * 27)
            msg = msg.format(str(w_beg), str(w_end))
            if self.log:
                self.output.write_log(msg)
            else:
                print(msg)

            self.data.read_mseed(w_beg, w_end, self.sampling_rate)

            daten, dsnr, dsnr_norm, dloc, map_ = self._compute(
                                                    w_beg, w_end,
                                                    self.data.signal,
                                                    self.data.availability)

            dcoord = self.lut.xyz2coord(dloc)

            self.output.file_sample_rate = self.output_sampling_rate
            coalescence_mSEED = self.output.write_decscan(coalescence_mSEED,
                                                          daten[:-1],
                                                          dsnr[:-1],
                                                          dsnr_norm[:-1],
                                                          dcoord[:-1, :],
                                                          self.sampling_rate)

            del daten, dsnr, dsnr_norm, dloc, map_

        print("=" * 126)

    def _compute(self, w_beg, w_end, signal, station_availability):

        sampling_rate = self.sampling_rate

        avail_idx = np.where(station_availability == 1)[0]
        sige = signal[0]
        sign = signal[1]
        sigz = signal[2]

        # Demeaning the data
        # sige -= np.mean(sige, axis=1)
        # sign -= np.mean(sign, axis=1)
        # sigz -= np.mean(sigz, axis=1)

        if self.deep_learning:
            msg = "Deep Learning coalescence under development."
            # msg = "Applying deep learning coalescence technique."
            print(msg)
            # dl = DeepLearningPhaseDetection(sige, sign, sigz, sampling_rate)
            # self.data.p_onset = DL.prob_P
            # self.data.s_onset = DL.prob_S
            # self.data.p_onset_raw = DL.prob_P
            # self.data.s_onset_raw = DL.prob_S
        else:
            p_onset_raw, p_onset = self._compute_p_onset(sigz, sampling_rate)
            s_onset_raw, s_onset = self._compute_s_onset(sige, sign, sampling_rate)
            self.data.p_onset = p_onset
            self.data.s_onset = s_onset
            self.data.p_onset_raw = p_onset_raw
            self.data.s_onset_raw = s_onset_raw

        p_s_onset = np.concatenate((self.data.p_onset, self.data.s_onset))
        p_s_onset[np.isnan(p_s_onset)] = 0

        p_ttime = self.lut.fetch_index("TIME_P", sampling_rate)
        s_ttime = self.lut.fetch_index("TIME_S", sampling_rate)
        ttime = np.c_[p_ttime, s_ttime]
        del p_ttime, s_ttime

        nchan, tsamp = p_s_onset.shape

        pre_smp = int(round(self.pre_pad * int(sampling_rate)))
        pos_smp = int(round(self.post_pad * int(sampling_rate)))
        nsamp = tsamp - pre_smp - pos_smp

        daten = 0.0 - pre_smp / sampling_rate

        ncell = tuple(self.lut.cell_count)

        map_ = np.zeros(ncell + (nsamp,), dtype=np.float64)

        dind = np.zeros(nsamp, np.int64)
        dsnr = np.zeros(nsamp, np.double)
        dsnr_norm = np.zeros(nsamp, np.double)

        ilib.scan(p_s_onset, ttime, pre_smp, pos_smp,
                  nsamp, map_, self.n_cores)
        ilib.detect(map_, dsnr, dind, 0, nsamp, self.n_cores)

        tmp = np.arange(w_beg + self.pre_pad,
                        w_end - self.post_pad + (1 / sampling_rate),
                        1 / sampling_rate)
        daten = [x.datetime for x in tmp]
        dsnr = np.exp((dsnr / (len(avail_idx) * 2)) - 1.0)
        dloc = self.lut.xyz2index(dind, inverse=True)

        # Determining the normalised coalescence through time
        sum_coa = np.sum(map_, axis=(0, 1, 2))
        map_ = map_ / sum_coa[np.newaxis, np.newaxis, np.newaxis, :]
        ilib.detect(map_, dsnr_norm, dind, 0, nsamp, self.n_cores)
        dsnr_norm = dsnr_norm * map_.shape[0] * map_.shape[1] * map_.shape[2]

        # Reset map to original coalescence value
        map_ = map_ * sum_coa[np.newaxis, np.newaxis, np.newaxis, :]

        return daten, dsnr, dsnr_norm, dloc, map_

    def _compute_p_onset(self, sig_z, sampling_rate):
        """
        Generates an onset function for the Z-component

        Parameters
        ----------
        sig_z : array-like
            Z-component time-series
        sampling_rate : int
            Sampling rate in hertz

        Returns
        -------


        """

        stw, ltw = self.p_onset_win
        stw = int(stw * sampling_rate) + 1
        ltw = int(ltw * sampling_rate) + 1
        sig_z = self._preprocess_p(sig_z, sampling_rate)
        self.filt_data["sigz"] = sig_z
        p_onset_raw, p_onset = onset(sig_z, stw, ltw,
                                     centred=self._onset_centred)
        self.onset_data["sigz"] = p_onset

        return p_onset_raw, p_onset

    def _preprocess_p(self, sig_z, sampling_rate):
        """
        Pre-processing method for Z-component

        Applies a butterworth bandpass filter.

        Parameters
        ----------
        sig_z : array-like
            Z-component time-series
        sampling_rate : int
            Sampling rate in hertz

        Returns
        -------
        A filtered version of the Z-component time-series

        """

        lc, hc, ord_ = self.p_bp_filter
        sig_z = filter(sig_z, sampling_rate, lc, hc, ord_)
        self.data.filtered_signal[2, :, :] = sig_z

        return sig_z

    def _compute_s_onset(self, sig_e, sig_n, sampling_rate):
        """
        Generates onset functions for the N- and E-components

        Parameters
        ----------
        sig_e : array-like
            E-component time-series
        sig_n : array-like
            N-component time-series
        sampling_rate : int
            Sampling rate in hertz

        Returns
        -------
        s_onset_raw :

        s_onset :

        """

        stw, ltw = self.s_onset_win
        stw = int(stw * sampling_rate) + 1
        ltw = int(ltw * sampling_rate) + 1
        sig_e, sig_n = self._preprocess_s(sig_e, sig_n, sampling_rate)
        self.filt_data["sige"] = sig_e
        self.filt_data["sign"] = sig_n
        s_e_onset_raw, s_e_onset = onset(sig_e, stw, ltw,
                                         centred=self._onset_centred)
        s_n_onset_raw, s_n_onset = onset(sig_n, stw, ltw,
                                         centred=self._onset_centred)
        self.onset_data["sige"] = s_e_onset
        self.onset_data["sign"] = s_n_onset
        s_onset = np.sqrt((s_e_onset ** 2 + s_n_onset ** 2) / 2.)
        s_onset_raw = np.sqrt((s_e_onset_raw ** 2 + s_n_onset_raw ** 2) / 2.)
        self.onset_data["sigs"] = s_onset

        return s_onset_raw, s_onset

    def _preprocess_s(self, sig_e, sig_n, sampling_rate):
        """
        Pre-processing method for N- and E-components

        Applies a butterworth bandpass filter.

        Parameters
        ----------
        sig_e : array-like
            E-component time-series
        sig_n : array-like
            N-component time-series
        sampling_rate : int
            Sampling rate in hertz

        Returns
        -------
        A filtered version of the N- and E-components time-series

        """
        lc, hc, ord_ = self.s_bp_filter
        sig_e = filter(sig_e, sampling_rate, lc, hc, ord_)
        sig_n = filter(sig_n, sampling_rate, lc, hc, ord_)
        self.data.filtered_signal[0, :, :] = sig_n
        self.data.filtered_signal[1, :, :] = sig_e

        return sig_e, sig_n

    def _trigger_scn(self, coa_val, start_time, end_time):

        if self.normalise_coalescence is True:
            coa_val["COA"] = coa_val["COA_N"]

        coa_val = coa_val[coa_val["COA"] >= self.detection_threshold]
        coa_val = coa_val[(coa_val["DT"] >= start_time) &
                          (coa_val["DT"] <= end_time)]

        coa_val = coa_val.reset_index(drop=True)

        if len(coa_val) == 0:
            msg = "No events triggered at this threshold"
            print(msg)
            return None

        event_cols = ["EventNum", "CoaTime", "COA_V", "COA_X", "COA_Y",
                      "COA_Z", "MinTime", "MaxTime"]

        ss = 1 / self.sampling_rate

        # Determine the initial triggered events
        init_events = pd.DataFrame(columns=event_cols)
        c = 0
        e = 1
        while c < len(coa_val) - 1:
            # Determining the index when above the level and maximum value
            d = c

            while coa_val["DT"].iloc[d] + ss == coa_val["DT"].iloc[d + 1]:
                d += 1
                if d + 1 >= len(coa_val) - 2:
                    d = len(coa_val) - 2
                    break

            min_idx = c
            max_idx = d
            val_idx = np.argmax(coa_val["COA"].iloc[np.arange(c, d + 1)])

            # Determining the times for min, max and max coalescence value
            t_min = coa_val["DT"].iloc[min_idx]
            t_max = coa_val["DT"].iloc[max_idx]
            t_val = coa_val["DT"].iloc[val_idx]

            COA_V = coa_val["COA"].iloc[val_idx]
            COA_X = coa_val["X"].iloc[val_idx]
            COA_Y = coa_val["Y"].iloc[val_idx]
            COA_Z = coa_val["Z"].iloc[val_idx]

            if (t_val - t_min) < self.marginal_window:
                t_min = t_val - self.marginal_window - self.minimum_repeat
            else:
                t_min = t_min - self.minimum_repeat

            if (t_max - t_val) < self.marginal_window:
                t_max = t_val + self.marginal_window + self.minimum_repeat
            else:
                t_max = t_max + self.minimum_repeat

            tmp = pd.DataFrame([[e, t_val,
                                COA_V, COA_X, COA_Y, COA_Z,
                                t_min, t_max]],
                               columns=event_cols)
            init_events = init_events.append(tmp, ignore_index=True)

            c = d + 1
            e += 1

        n_evts = len(init_events)
        evt_num = np.ones((n_evts), dtype=int)

        count = 1
        for i, event in init_events.iterrows():
            evt_num[i] = count
            if (i + 1 < n_evts) and ((event["MaxTime"] - (init_events["CoaTime"].iloc[i + 1] - self.marginal_window)) < 0):
                count += 1
        init_events["EventNum"] = evt_num

        events = pd.DataFrame(columns=event_cols)
        for i in range(1, count + 1):
            tmp = init_events[init_events["EventNum"] == i]
            tmp = tmp.reset_index(drop=True)
            j = np.argmax(tmp["COA_V"])
            min_mt = np.min(tmp["MinTime"])
            max_mt = np.max(tmp["MaxTime"])
            event = pd.DataFrame([[i, tmp["CoaTime"].iloc[j],
                                   tmp["COA_V"].iloc[j],
                                   tmp["COA_X"].iloc[j],
                                   tmp["COA_Y"].iloc[j],
                                   tmp["COA_Z"].iloc[j],
                                   min_mt,
                                   max_mt]],
                                 columns=event_cols)
            events = events.append(event, ignore_index=True)

        evt_id = events["CoaTime"].astype(str)
        for char_ in ["-", ":", ".", " ", "Z", "T"]:
            evt_id = evt_id.str.replace(char_, "")
        events["EventID"] = evt_id

        if len(events) == 0:
            events = None

        return events

    def _gaussian_trigger(self, onset, phase, start_time, p_arrival, s_arrival,
                          p_ttime, s_ttime):
        """
        Fit a Gaussian to the onset function in order to make a time pick.

        Uses knowledge of approximate trigger index, the short-term average onset
        window and the signal sampling rate.

        Parameters
        ----------
        onset :
            Onset function
        phase : str
            Phase name ("P" or "S")
        start_time : UTCDateTime object
            Start time of data (w_beg)
        p_arrival : UTCDateTime object
            Time when P-phase is expected to arrive based on best location.
        s_arrival : UTCDateTime object
            Time when S-phase is expected to arrive based on best location.
        p_ttime : UTCDateTime object
            Traveltime of P-phase
        s_ttime : UTCDateTime object
            Traveltime of S-phase

        Returns
        -------
        gaussian_fit : dictionary
            gaussian fit parameters

        max_onset : float
            amplitude of gaussian fit to onset function

        sigma : float
            sigma of gaussian fit to onset function

        mean : UTCDateTime
            mean of gaussian fit to onset function == pick time


        """

        msg = "Fitting Gaussian for {} - {} - {}"
        msg = msg.format(phase, str(start_time), str(p_arrival))
        # print(msg)

        # Determine indices of P and S trigger times
        pt_idx = int((p_arrival - start_time) * self.sampling_rate)
        st_idx = int((s_arrival - start_time) * self.sampling_rate)

        # Determine P and S pick window upper and lower bounds based on
        # (P-S)/2 -- either this or the next window definition will be
        # used depending on which is wider.
        pmin_idx = int(pt_idx - (st_idx - pt_idx) / 2) # unnecessary?
        pmax_idx = int(pt_idx + (st_idx - pt_idx) / 2)
        smin_idx = int(st_idx - (st_idx - pt_idx) / 2)
        smax_idx = int(st_idx + (st_idx - pt_idx) / 2) # unnecessary?

        # Check if index falls outside length of onset function; if so set
        # window to start/end at start/end of data.
        for idx in [pmin_idx, pmax_idx, smin_idx, smax_idx]:
            if idx < 0:
                idx = 0
            if idx > len(onset):
                idx = len(onset)

        # Defining the bounds to search for the event over
        # Determine P and S pick window upper and lower bounds based on
        # set percentage of total travel time, plus marginal window

        # window based on self.percent_tt of P/S travel time
        pp_ttime = p_ttime * self.percent_tt
        ps_ttime = s_ttime * self.percent_tt

        # Add length of marginal window to this. Convert to index.
        P_idxmin_new = int(pt_idx - int((self.marginal_window + pp_ttime)
                                        * self.sampling_rate))
        P_idxmax_new = int(pt_idx + int((self.marginal_window + pp_ttime)
                                        * self.sampling_rate))
        S_idxmin_new = int(st_idx - int((self.marginal_window + ps_ttime)
                                        * self.sampling_rate))
        S_idxmax_new = int(st_idx + int((self.marginal_window + ps_ttime)
                                        * self.sampling_rate))

        # Setting so the search region can"t be bigger than (P-S)/2.
        # Compare these two window definitions. If (P-S)/2 window is
        # smaller then use this (to avoid picking the wrong phase).
        P_idxmin = np.max([pmin_idx, P_idxmin_new])
        P_idxmax = np.min([pmax_idx, P_idxmax_new])
        S_idxmin = np.max([smin_idx, S_idxmin_new])
        S_idxmax = np.min([smax_idx, S_idxmax_new])

        # Setting parameters depending on the phase
        if phase == "P":
            sta_winlen = self.p_onset_win[0]
            win_min = P_idxmin
            win_max = P_idxmax
        if phase == "S":
            sta_winlen = self.s_onset_win[0]
            win_min = S_idxmin
            win_max = S_idxmax

        # Find index of maximum value of onset function in the appropriate
        # pick window
        max_onset = np.argmax(onset[win_min:win_max]) + win_min
        # Trim the onset function in the pick window
        onset_trim = onset[win_min:win_max]

        # Only keep the onset function outside the pick windows to
        # calculate the pick threshold
        onset_threshold = onset.copy()
        onset_threshold[P_idxmin:P_idxmax] = -1
        onset_threshold[S_idxmin:S_idxmax] = -1
        onset_threshold = onset_threshold[onset_threshold > -1]

        # Calculate the pick threshold: either user-specified percentile of
        # data outside pick windows, or 88th percentile within the relevant
        # pick window (whichever is bigger).
        threshold = np.percentile(onset_threshold, self.pick_threshold * 100)
        threshold_window = np.percentile(onset_trim, 88)
        threshold = np.max([threshold, threshold_window])

        # Remove data within the pick window that is lower than the threshold
        tmp = (onset_trim - threshold).any() > 0

        # If there is any data that meets this requirement...
        if onset[max_onset] >= threshold and tmp:
            exceedence = np.where((onset_trim - threshold) > 0)[0]
            exceedence_dist = np.zeros(len(exceedence))

            # Really faffy process to identify the period of data which is
            # above the threshold around the highest value of the onset
            # function.
            d = 1
            e = 0
            while e < len(exceedence_dist) - 1:
                if e == len(exceedence_dist):
                    exceedence_dist[e] = d
                else:
                    if exceedence[e + 1] == exceedence[e] + 1:
                        exceedence_dist[e] = d
                    else:
                        exceedence_dist[e] = d
                        d += 1
                e += 1

            # Find the indices for this period of data
            tmp = exceedence_dist[np.argmax(onset_trim[exceedence])]
            tmp = np.where(exceedence_dist == tmp)

            # Add one data point below the threshold at each end of this period
            gau_idxmin = exceedence[tmp][0] + win_min - 1
            gau_idxmax = exceedence[tmp][-1] + win_min + 2

            # Initial guess for gaussian half-width based on onset function
            # STA window length
            data_half_range = int(sta_winlen * self.sampling_rate / 2)

            # Select data to fit the gaussian to
            x_data = np.arange(gau_idxmin, gau_idxmax, dtype=float)
            x_data = x_data / self.sampling_rate
            y_data = onset[gau_idxmin:gau_idxmax]

            # Convert indices to times
            x_data_dt = np.array([])
            for i in range(len(x_data)):
                x_data_dt = np.hstack([x_data_dt, start_time + x_data[i]])

            # Try to fit a gaussian.
            try:
                # Initial parameters are:
                #  height = max value of onset function
                #  mean   = time of max value
                #  sigma  = data half-range (calculated above)
                p0 = [np.max(y_data),
                      float(gau_idxmin + np.argmax(y_data)) / self.sampling_rate,
                      data_half_range / self.sampling_rate]

                # Do the fit
                popt, pcov = curve_fit(gaussian_1d, x_data, y_data, p0)

                # Results:
                #  popt = [height, mean (seconds), sigma (seconds)]
                #  pcov not used
                max_onset = popt[0]
                # Convert mean (pick time) to time
                mean = start_time + float(popt[1])
                sigma = np.absolute(popt[2])

                gaussian_fit = {"popt": popt,
                                "xdata": x_data,
                                "xdata_dt": x_data_dt,
                                "PickValue": max_onset,
                                "PickThreshold": threshold}

            # If curve_fit fails. Will also spit error message to stdout,
            # though this can be suppressed  - see warnings.filterwarnings()
            except:
                gaussian_fit = self.DEFAULT_GAUSSIAN_FIT
                gaussian_fit["PickThreshold"] = threshold

                sigma = -1
                mean = -1
                max_onset = -1

        # If onset function does not exceed threshold in pick window
        else:
            gaussian_fit = self.DEFAULT_GAUSSIAN_FIT
            gaussian_fit["PickThreshold"] = threshold

            sigma = -1
            mean = -1
            max_onset = -1

        return gaussian_fit, max_onset, sigma, mean

    def _arrival_trigger(self, max_coa, event_name):
        """
        Determines arrival times for triggered earthquakes.

        Parameters
        ----------
        max_coa : pandas DataFrame object
            DataFrame containing the maximum coalescence values for a
            given event
        event_name : str
            Event ID - used for saving the picks file

        Returns
        -------

        """

        p_onset = self.data.p_onset
        s_onset = self.data.s_onset
        start_time = self.data.start_time

        max_coa_crd = np.array([max_coa[["X", "Y", "Z"]].values])
        max_coa_xyz = np.array(self.lut.xyz2coord(max_coa_crd,
                                                  inverse=True)).astype(int)[0]

        p_ttime = self.lut.value_at("TIME_P", max_coa_xyz)[0]
        s_ttime = self.lut.value_at("TIME_S", max_coa_xyz)[0]

        # Determining the stations that can be picked on and the phases
        picks = pd.DataFrame(index=np.arange(0, 2 * len(p_onset)),
                             columns=["Name", "Phase", "ModelledTime",
                                      "PickTime", "PickError","SNR"])

        p_gauss = np.array([])
        s_gauss = np.array([])
        idx = 0
        for i in range(len(p_onset)):
            p_arrival = max_coa["DT"] + p_ttime[i]
            s_arrival = max_coa["DT"] + s_ttime[i]

            if self.picking_mode == "Gaussian":
                for phase in ["P", "S"]:
                    if phase == "P":
                        onset = p_onset[i]
                        arrival = p_arrival
                    else:
                        onset = s_onset[i]
                        arrival = s_arrival

                    msg = "Fitting Gaussian for  {}  -  {}"
                    msg = msg.format(phase, str(arrival))
                    # if self.log:
                    #     self.output.write_log(msg)
                    # else:
                    #     print(msg)

                    gau, max_onset, err, mn = self._gaussian_trigger(onset,
                                                                     phase,
                                                                     start_time,
                                                                     p_arrival,
                                                                     s_arrival,
                                                                     p_ttime[i],
                                                                     s_ttime[i])

                    if phase == "P":
                        p_gauss = np.hstack([p_gauss, gau])
                    else:
                        s_gauss = np.hstack([s_gauss, gau])

                    picks.iloc[idx] = [self.lut.station_data['Name'][i],
                                       phase, arrival, mn, err, max_onset]
                    idx += 1

        self.output.write_picks(picks, event_name)

        return picks, p_gauss, s_gauss

    def _gaufilt3d(self, map_3d, sgm=0.8, shp=None):
        """


        Parameters
        ----------
        map_3d : 3-d array
                 marginalised 3d coalescence map

        sgm : float / int
              sigma value (in grid cells) for the 3d gaussian filter function
              --> bigger sigma leads to more aggressive (long wavelength) smoothing.

        shp : array-like, optional
            Shape of volume


        Returns
        -------
        smoothed_map_3d : 3-d array
                          gaussian smoothed 3d coalescence map

        """

        if shp is None:
            shp = map_3d.shape
        nx, ny, nz = shp

        # Normalise
        map_3d = map_3d/np.nanmax(map_3d)

        # Construct 3d gaussian filter
        flt = gaussian_3d(nx, ny, nz, sgm, 0.)
        # Convolve map_3d and 3d gaussian filter
        smoothed_map_3d = fftconvolve(map_3d, flt, mode="same")

        # Mirror and convolve again (to avoid 'phase-shift')
        smoothed_map_3d = smoothed_map_3d[::-1,::-1,::-1]/np.nanmax(smoothed_map_3d)
        smoothed_map_3d = fftconvolve(smoothed_map_3d, flt, mode="same")

        # Final mirror and normalise
        smoothed_map_3d = smoothed_map_3d[::-1,::-1,::-1]/np.nanmax(smoothed_map_3d)

        return smoothed_map_3d

    def _mask3d(self, n, i, win):
        """


        Parameters
        ----------
        n :

        i :

        win :

        Returns
        -------
        mask


        """

        n = np.array(n)
        i = np.array(i)
        w2 = (win-1)//2
        x1, y1, z1 = np.clip(i - w2, 0 * n, n)
        x2, y2, z2 = np.clip(i + w2 + 1, 0 * n, n)
        mask = np.zeros(n, dtype=np.bool)
        mask[x1:x2, y1:y2, z1:z2] = True

        # removing true if value is np.nan
        #mask[x1:x2, y1:y2, z1:z2] = ~np.isnan(self.coa_map)[x1:x2, y1:y2, z1:z2]


        return mask

    def _covfit3d(self, coa_map, thresh=0.88, win=None):
        """


        Parameters
        ----------
        coa_map : 3-d array
                  marginalised 3d coalescence map

        thresh : float (between 0 and 1)
                 cut-off threshold (fractional percentile) to trim coa_map; only
                 data above this percentile will be retained, optional

        win : int
              window of grid cells (+/-win in x, y and z) around max value in coa_map
              to perform the fit over, optional

        Returns
        -------
        loc_cov : array-like
                  [x, y, z] expectation location from covariance fit

        loc_err_cov : array-like
                      [x_err, y_err, z_err] one sigma uncertainties associated with
                      loc_cov


        """
        # Normalise!!
        coa_map = coa_map/(np.nanmax(coa_map))

        # Determining Covariance Location and Error
        nx, ny, nz = coa_map.shape
        mx, my, mz = np.unravel_index(np.nanargmax(coa_map), coa_map.shape)

        # If window is specified, clip the grid to only look here.
        if win:
            flg = np.logical_and(coa_map > thresh,#mval * np.exp(-(thresh * thresh) / 2),
                               self._mask3d([nx, ny, nz], [mx, my, mz], win))
            ix, iy, iz = np.where(flg)
            print('Variables',min(ix), max(ix), min(iy), max(iy), min(iz), max(iz))
        else:
            flg = np.where(coa_map > thresh, True, False)
            ix, iy, iz = nx, ny, nz

        smp_weights = coa_map.flatten()
        smp_weights[~flg.flatten()] = np.nan

        lc = self.lut.cell_count
        # Ordering below due to handedness of the grid
        ly, lx, lz = np.meshgrid(np.arange(lc[1]),
                                 np.arange(lc[0]),
                                 np.arange(lc[2]))
        x_samples = lx.flatten() * self.lut.cell_size[0]
        y_samples = ly.flatten() * self.lut.cell_size[1]
        z_samples = lz.flatten() * self.lut.cell_size[2]

        ssw = np.nansum(smp_weights)
        #print(ssw)

        # Expectation values:
        x_expect = np.nansum(smp_weights * x_samples) / ssw
        y_expect = np.nansum(smp_weights * y_samples) / ssw
        z_expect = np.nansum(smp_weights * z_samples) / ssw

        #print(x_expect)

        # if self.log:
        #     self.output.write_log(msg)
        # else:
        #     print(msg)

        # Covariance matrix:
        cov_matrix = np.zeros((3, 3))
        cov_matrix[0, 0] = np.nansum(smp_weights
                                  * (x_samples - x_expect) ** 2) / ssw
        cov_matrix[1, 1] = np.nansum(smp_weights
                                  * (y_samples - y_expect) ** 2) / ssw
        cov_matrix[2, 2] = np.nansum(smp_weights
                                  * (z_samples - z_expect) ** 2) / ssw
        cov_matrix[0, 1] = np.nansum(smp_weights
                                  * (x_samples - x_expect)
                                  * (y_samples - y_expect)) / ssw
        cov_matrix[1, 0] = cov_matrix[0, 1]
        cov_matrix[0, 2] = np.nansum(smp_weights
                                  * (x_samples - x_expect)
                                  * (z_samples - z_expect)) / ssw
        cov_matrix[2, 0] = cov_matrix[0, 2]
        cov_matrix[1, 2] = np.nansum(smp_weights
                                  * (y_samples - y_expect)
                                  * (z_samples - z_expect)) / ssw
        cov_matrix[2, 1] = cov_matrix[1, 2]

        # Determining the maximum location, and taking 2xgrid cells positive
        # and negative for location in each dimension\

        expect_vector_cov = np.array([x_expect,
                                      y_expect,
                                      z_expect],
                                     dtype=float)
        loc_cov_gc = np.array([[expect_vector_cov[0] / self.lut.cell_size[0],
                             expect_vector_cov[1] / self.lut.cell_size[1],
                             expect_vector_cov[2] / self.lut.cell_size[2]]])

        loc_err_cov = np.array([np.sqrt(cov_matrix[0, 0]),
                                np.sqrt(cov_matrix[1, 1]),
                                np.sqrt(cov_matrix[2, 2])])


        loc_cov = self.lut.xyz2coord(self.lut.xyz2loc(loc_cov_gc, inverse=True))[0]


        return loc_cov, loc_err_cov



    def _gaufit3d(self, coa_map, lx=None, ly=None, lz=None,
                  thresh=0., win=7):
        """


        Parameters
        ----------
        coa_map : 3-d array
                  marginalised 3d coalescence map

        lx : , optional

        ly : , optional

        lz : , optional

        thresh : float (between 0 and 1)
                 cut-off threshold (percentile) to trim coa_map: only
                 data above this percentile will be retained, optional

        win : int
              window of grid cells (+/- win in x, y and z) around max value in coa_map
              to perform the fit over, optional


        Returns
        -------
        loc_gau : array-like
                  [x, y, z] expectation location from 3d gaussian fit

        loc_gau_err : array-like
                      [x_err, y_err, z_err] one sigma uncertainties from 3d gaussian fit


        """
        nx, ny, nz = coa_map.shape
        mx, my, mz = np.unravel_index(np.nanargmax(coa_map), coa_map.shape)
        mval = coa_map[mx, my, mz]
        # Only use grid cells above threshold value, and within the specified
        # window around the coalescence peak
        flg = np.logical_and(coa_map > thresh,
                            self._mask3d([nx, ny, nz], [mx, my, mz], win))

        ix, iy, iz = np.where(flg)

        # Subtract mean of 3d coalescence map so it is more appropriately
        # approximated by a gaussian (which goes to zero at infinity)
        coa_map = coa_map - np.nanmean(coa_map)

        # print('Variables',min(ix), max(ix), min(iy), max(iy), min(iz), max(iz))
        # if self.log:
        #     self.output.write_log(msg)
        # else:
        #     print(msg)

        ncell = len(ix)

        if not lx:
            lx = np.arange(nx)
            ly = np.arange(ny)
            lz = np.arange(nz)

        if lx.ndim == 3:
            iloc = [lx[mx, my, mz], ly[mx, my, mz], lz[mx, my, mz]]
            x = lx[ix, iy, iz] - iloc[0]
            y = ly[ix, iy, iz] - iloc[1]
            z = lz[ix, iy, iz] - iloc[2]
        else:
            iloc = [lx[mx], ly[my], lz[mz]]
            x = lx[ix] - iloc[0]
            y = ly[iy] - iloc[1]
            z = lz[iz] - iloc[2]

        X = np.c_[x * x, y * y, z * z,
                  x * y, x * z, y * z,
                  x, y, z, np.ones(ncell)].T
        Y = -np.log(np.clip(coa_map.astype(np.float64)[ix, iy, iz],
                            1e-300, np.inf))

        X_inv = np.linalg.pinv(X)
        P = np.matmul(Y, X_inv)
        G = -np.array([2 * P[0], P[3], P[4],
                       P[3], 2 * P[1], P[5],
                       P[4], P[5], 2 * P[2]]).reshape((3, 3))
        H = np.array([P[6], P[7], P[8]])
        loc = np.matmul(np.linalg.inv(G), H)
        cx, cy, cz = loc

        K = P[9]             \
            - P[0] * cx ** 2 \
            - P[1] * cy ** 2 \
            - P[2] * cz ** 2 \
            - P[3] * cx * cy \
            - P[4] * cx * cz \
            - P[5] * cy * cz \

        M = np.array([P[0], P[3] / 2, P[4] / 2,
                      P[3] / 2, P[1], P[5] / 2,
                      P[4] / 2, P[5] / 2, P[2]]).reshape(3, 3)
        egv, vec = np.linalg.eig(M)
        sgm = np.sqrt(0.5 / np.clip(np.abs(egv), 1e-10, np.inf))/2
        val = np.exp(-K)
        csgm = np.sqrt(0.5 / np.clip(np.abs(M.diagonal()), 1e-10, np.inf))

        gau_3d = [loc + iloc, vec, sgm, csgm, val]

        # Converting the grid location to X,Y,Z
        xyz = self.lut.xyz2loc(np.array([[gau_3d[0][0],
                                          gau_3d[0][1],
                                          gau_3d[0][2]]]),
                               inverse=True)
        loc_gau = self.lut.xyz2coord(xyz)[0]


        loc_gau_err = np.array([gau_3d[2][0] * self.lut.cell_size[0],
                                gau_3d[2][1] * self.lut.cell_size[1],
                                gau_3d[2][2] * self.lut.cell_size[2]])

        return loc_gau, loc_gau_err


    def _splineloc(self, coa_map, win=5, upscale=10):
        """


        Parameters
        ----------
        coa_map : 3-d array
                  marginalised 3d coalescence map

        win : int
              window of grid cells (+/- win in x, y and z) around max value in coa_map
              to perform the fit over, optional

        upsacel : int
              upscaling factor to increase the grid ready for spline fitting


        Returns
        -------
        loc : array-like
                  [x, y, z] expectation location from spline interpolation



        """
        #np.save('Coamap',coa_map)
        nx, ny, nz = coa_map.shape
        n = np.array([nx, ny, nz])

        mx, my, mz = np.unravel_index(np.nanargmax(coa_map), coa_map.shape)
        i = np.array([mx, my, mz])
        mval = coa_map[mx, my, mz]


        # Determining window about maximum value and trimming coa grid
        w2 = (win - 1)//2
        x1, y1, z1 = np.clip(i - w2, 0 * n, n)
        x2, y2, z2 = np.clip(i + w2 + 1, 0 * n, n)


        # If subgrid is not close to the edge
        if (x2 - x1) == (y2 - y1) == (z2 - z1):
            coa_map_trim = coa_map[x1:x2, y1:y2, z1:z2]

            # Defining the original interpolation function
            xo = np.linspace(0, coa_map_trim.shape[0] - 1, coa_map_trim.shape[0])
            yo = np.linspace(0, coa_map_trim.shape[1] - 1, coa_map_trim.shape[1])
            zo = np.linspace(0, coa_map_trim.shape[2] - 1, coa_map_trim.shape[2])
            xog, yog, zog = np.meshgrid(xo, yo, zo)
            interpgrid = Rbf(xog.flatten(), yog.flatten(), zog.flatten(),
                             coa_map_trim.flatten(),
                             function='cubic')

            # Creating the new grid for the data
            xx = np.linspace(0, coa_map_trim.shape[0] - 1, (coa_map_trim.shape[0] - 1) * upscale + 1)
            yy = np.linspace(0, coa_map_trim.shape[1] - 1, (coa_map_trim.shape[1] - 1) * upscale + 1)
            zz = np.linspace(0, coa_map_trim.shape[2] - 1, (coa_map_trim.shape[2] - 1) * upscale + 1)
            xxg, yyg, zzg = np.meshgrid(xx, yy, zz)
            coa_map_int = interpgrid(xxg.flatten(), yyg.flatten(), zzg.flatten()).reshape(xxg.shape)
            mxi, myi, mzi = np.unravel_index(np.nanargmax(coa_map_int), coa_map_int.shape)
            mxi = mxi/upscale + x1
            myi = myi/upscale + y1
            mzi = mzi/upscale + z1
            print('Spline loc: ', mxi, myi, mzi)
            print('Gridded loc:', mx, my, mz)

            # Run check that spline location is within grid-cell
            if (abs(mx - mxi) > 1) or (abs(my - myi) > 1) or (abs(mz - mzi) > 1):
                msg = "Spline warning: location outside grid-cell with maximum coalescence value"
                if self.log:
                    self.output.write_log(msg)
                else:
                    print(msg)

            xyz = self.lut.xyz2loc(np.array([[mxi, myi, mzi]]), inverse=True)
            loc = self.lut.xyz2coord(xyz)[0]

            # Run check that spline location is within window (should be impossible not to..)
            if (abs(mx - mxi) > w2) or (abs(my - myi) > w2) or (abs(mz - mzi) > w2):
                msg = "Spline error: location outside interpolation window!\n Gridded Location returned"
                if self.log:
                    self.output.write_log(msg)
                else:
                    print(msg)

                xyz = self.lut.xyz2loc(np.array([[mx, my, mz]]), inverse=True)
                loc = self.lut.xyz2coord(xyz)[0]

        else:
            msg = "Spline error: interpolation window crosses edge of grid!\n Gridded Location returned"
            if self.log:
                self.output.write_log(msg)
            else:
                print(msg)


            xyz = self.lut.xyz2loc(np.array([[mx, my, mz]]), inverse=True)
            loc = self.lut.xyz2coord(xyz)[0]

        return loc

    def _location_error(self, map_4d):
        """

        Parameters
        ----------
        map_4d : 4-d array
                 4d coalescence grid output from _compute()


        Returns
        -------
        loc : array-like
              [x, y, z] best-fit location from local fit to the coalescence grid

        loc_err : array-like
                  [x_err, y_err, z_err] one sigma uncertainties associated with loc

        loc_cov : array-like
                  [x, y, z] best-fit location from covariance fit over entire 3d grid
                  (most commonly after filtering above a certain percentile).

        loc_err_cov : array-like
                      [x_err, y_err, z_err] one sigma uncertainties associated with loc_cov


        """

        # MARGINALISE: Determining the coalescence 3D map
        self.coa_map = np.log(np.sum(np.exp(map_4d), axis=-1))

        # Normalise
        self.coa_map = self.coa_map/np.max(self.coa_map)

        ## Determining the location error as an error-ellipse
        # Calculate global covariance
        loc_cov, loc_err_cov  = self._covfit3d(np.copy(self.coa_map))

        # Fit local gaussian error ellipse
        loc_spline = self._splineloc(np.copy(self.coa_map))
        smoothed_coa_map = self._gaufilt3d(np.copy(self.coa_map))
        loc, loc_err = self._gaufit3d(np.copy(smoothed_coa_map), thresh=0.)

        # Calculate local covariance
        # loc, loc_err  = self._covfit3d(np.copy(coa_3d),thresh=0.88)#self._gaufit3d(coa_3d,thresh=0.0)

        return loc_spline, loc, loc_err, loc_cov, loc_err_cov
