#!/usr/bin/env python3

from datetime import datetime
from pathlib import Path

import matplotlib
import matplotlib.font_manager
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np

from ..shaper_calibrate import (CalibrationData, CalibrationResult,
                                ShaperCalibrate)
from .common import (compute_mechanical_parameters, compute_spectrogram,
                     detect_peaks, load_accelerometer_data)

matplotlib.use('Agg')

PEAKS_DETECTION_THRESHOLD = 0.05
PEAKS_EFFECT_THRESHOLD = 0.12
SPECTROGRAM_LOW_PERCENTILE_FILTER = 5
MAX_SMOOTHING = 0.1

KLIPPAIN_COLORS = {
    "purple": "#70088C",
    "dark_purple": "#150140",
    "dark_orange": "#F24130"
}

######################################################################
# Computation
######################################################################


# Find the best shaper parameters using Klipper's official algorithm selection with
# a proper precomputed damping ratio (zeta) and using the configured printer SQV value
def calibrate_shaper(
    data: np.ndarray, max_smoothing: float | None, scv: float, max_freq: float
) -> tuple[str, list[CalibrationResult], CalibrationData, float, float]:
    helper = ShaperCalibrate(printer=None)
    calibration_data = helper.process_accelerometer_data(data)
    calibration_data.normalize_to_frequencies()

    fr, zeta, _ = compute_mechanical_parameters(calibration_data.psd_sum,
                                                calibration_data.freq_bins)

    shaper: CalibrationResult | None
    all_shapers: list[CalibrationResult] | None
    shaper, all_shapers = helper.find_best_shaper(calibration_data,
                                                  shapers=None,
                                                  damping_ratio=zeta,
                                                  scv=scv,
                                                  shaper_freqs=None,
                                                  max_smoothing=max_smoothing,
                                                  test_damping_ratios=None,
                                                  max_freq=max_freq,
                                                  logger=print)

    if shaper is None:
        print('\n-> No recommended shaper')
        raise ValueError('Failed to find a shaper')

    print(
        f'\n-> '
        f'Recommended shaper is {shaper.name.upper()} @ {shaper.freq:.1f} Hz (when using '
        f'a square corner velocity of {scv:.1f} and a computed damping ratio of {zeta:.3f})'
    )

    return shaper.name, all_shapers, calibration_data, fr, zeta


######################################################################
# Graphing
######################################################################


def plot_freq_response(ax, calibration_data, shapers, performance_shaper, peaks,
                       peaks_freqs, peaks_threshold, fr, zeta, max_freq):
    freqs = calibration_data.freqs
    psd = calibration_data.psd_sum
    px = calibration_data.psd_x
    py = calibration_data.psd_y
    pz = calibration_data.psd_z

    fontP = matplotlib.font_manager.FontProperties()
    fontP.set_size('x-small')

    ax.set_xlabel('Frequency (Hz)')
    ax.set_xlim([0, max_freq])
    ax.set_ylabel('Power spectral density')
    ax.set_ylim([0, psd.max() + psd.max() * 0.05])

    ax.plot(freqs, psd, label='X+Y+Z', color='purple', zorder=5)
    ax.plot(freqs, px, label='X', color='red')
    ax.plot(freqs, py, label='Y', color='green')
    ax.plot(freqs, pz, label='Z', color='blue')

    ax.xaxis.set_minor_locator(matplotlib.ticker.MultipleLocator(5))
    ax.yaxis.set_minor_locator(matplotlib.ticker.AutoMinorLocator())
    ax.ticklabel_format(axis='y', style='scientific', scilimits=(0, 0))
    ax.grid(which='major', color='grey')
    ax.grid(which='minor', color='lightgrey')

    ax2 = ax.twinx()
    ax2.yaxis.set_visible(False)

    lowvib_shaper_vibrs = float('inf')
    lowvib_shaper = None
    lowvib_shaper_freq = None
    lowvib_shaper_accel = 0
    lowvib_shaper_vals = None

    performance_shaper_freq = None
    performance_shaper_vibr = None
    performance_shaper_vals = None

    # Draw the shappers curves and add their specific parameters in the legend
    # This adds also a way to find the best shaper with a low level of vibrations (with a resonable level of smoothing)
    for shaper in shapers:
        shaper_max_accel = round(shaper.max_accel / 100.) * 100.
        label = "%s (%.1f Hz, vibr=%.1f%%, sm~=%.2f, accel<=%.f)" % (
            shaper.name.upper(), shaper.freq, shaper.vibrs * 100.,
            shaper.smoothing, shaper_max_accel)
        ax2.plot(freqs, shaper.vals, label=label, linestyle='dotted')

        # Get the performance shaper
        if shaper.name == performance_shaper:
            performance_shaper_freq = shaper.freq
            performance_shaper_vibr = shaper.vibrs * 100.
            performance_shaper_vals = shaper.vals

        # Get the low vibration shaper
        if (shaper.vibrs * 100 < lowvib_shaper_vibrs or
            (shaper.vibrs * 100 == lowvib_shaper_vibrs and shaper_max_accel
             > lowvib_shaper_accel)) and shaper.smoothing < MAX_SMOOTHING:
            lowvib_shaper_accel = shaper_max_accel
            lowvib_shaper = shaper.name
            lowvib_shaper_freq = shaper.freq
            lowvib_shaper_vibrs = shaper.vibrs * 100
            lowvib_shaper_vals = shaper.vals

    if any(p is None for p in [
            performance_shaper_freq, performance_shaper_vibr,
            performance_shaper_vals
    ]):
        raise ValueError('Failed to find a performance shaper')

    # User recommendations are added to the legend: one is Klipper's original suggestion that is usually good for performances
    # and the other one is the custom "low vibration" recommendation that looks for a suitable shaper that doesn't have excessive
    # smoothing and that have a lower vibration level. If both recommendation are the same shaper, or if no suitable "low
    # vibration" shaper is found, then only a single line as the "best shaper" recommendation is added to the legend
    if lowvib_shaper != None and lowvib_shaper != performance_shaper and lowvib_shaper_vibrs <= performance_shaper_vibr:
        ax2.plot([], [],
                 ' ',
                 label="Recommended performance shaper: %s @ %.1f Hz" %
                 (performance_shaper.upper(), performance_shaper_freq))
        ax.plot(freqs,
                psd * performance_shaper_vals,
                label='With %s applied' % (performance_shaper.upper()),
                color='cyan')
        ax2.plot([], [],
                 ' ',
                 label="Recommended low vibrations shaper: %s @ %.1f Hz" %
                 (lowvib_shaper.upper(), lowvib_shaper_freq))
        ax.plot(freqs,
                psd * lowvib_shaper_vals,
                label='With %s applied' % (lowvib_shaper.upper()),
                color='lime')
    else:
        ax2.plot([], [],
                 ' ',
                 label="Recommended best shaper: %s @ %.1f Hz" %
                 (performance_shaper.upper(), performance_shaper_freq))
        ax.plot(freqs,
                psd * performance_shaper_vals,
                label='With %s applied' % (performance_shaper.upper()),
                color='cyan')

    # And the estimated damping ratio is finally added at the end of the legend
    ax2.plot([], [], ' ', label="Estimated damping ratio (ζ): %.3f" % (zeta))

    # Draw the detected peaks and name them
    # This also draw the detection threshold and warning threshold (aka "effect zone")
    ax.plot(peaks_freqs, psd[peaks], "x", color='black', markersize=8)
    for idx, peak in enumerate(peaks):
        if psd[peak] > peaks_threshold[1]:
            fontcolor = 'red'
            fontweight = 'bold'
        else:
            fontcolor = 'black'
            fontweight = 'normal'
        ax.annotate(f"{idx+1}", (freqs[peak], psd[peak]),
                    textcoords="offset points",
                    xytext=(8, 5),
                    ha='left',
                    fontsize=13,
                    color=fontcolor,
                    weight=fontweight)
    ax.axhline(y=peaks_threshold[0],
               color='black',
               linestyle='--',
               linewidth=0.5)
    ax.axhline(y=peaks_threshold[1],
               color='black',
               linestyle='--',
               linewidth=0.5)
    ax.fill_between(freqs,
                    0,
                    peaks_threshold[0],
                    color='green',
                    alpha=0.15,
                    label='Relax Region')
    ax.fill_between(freqs,
                    peaks_threshold[0],
                    peaks_threshold[1],
                    color='orange',
                    alpha=0.2,
                    label='Warning Region')

    # Add the main resonant frequency and damping ratio of the axis to the graph title
    ax.set_title("Axis Frequency Profile (ω0=%.1fHz, ζ=%.3f)" % (fr, zeta),
                 fontsize=14,
                 color=KLIPPAIN_COLORS['dark_orange'],
                 weight='bold')
    ax.legend(loc='upper left', prop=fontP)
    ax2.legend(loc='upper right', prop=fontP)

    return


# Plot a time-frequency spectrogram to see how the system respond over time during the
# resonnance test. This can highlight hidden spots from the standard PSD graph from other harmonics
def plot_spectrogram(ax, t, bins, pdata, peaks, max_freq):
    ax.set_title("Time-Frequency Spectrogram",
                 fontsize=14,
                 color=KLIPPAIN_COLORS['dark_orange'],
                 weight='bold')

    # We need to normalize the data to get a proper signal on the spectrogram
    # However, while using "LogNorm" provide too much background noise, using
    # "Normalize" make only the resonnance appearing and hide interesting elements
    # So we need to filter out the lower part of the data (ie. find the proper vmin for LogNorm)
    vmin_value = np.percentile(pdata, SPECTROGRAM_LOW_PERCENTILE_FILTER)

    # Draw the spectrogram using imgshow that is better suited here than pcolormesh since its result is already rasterized and
    # we doesn't need to keep vector graphics when saving to a final .png file. Using it also allow to
    # save ~150-200MB of RAM during the "fig.savefig" operation.
    cm = 'inferno'
    norm = matplotlib.colors.LogNorm(vmin=vmin_value)
    ax.imshow(pdata.T,
              norm=norm,
              cmap=cm,
              aspect='auto',
              extent=[t[0], t[-1], bins[0], bins[-1]],
              origin='lower',
              interpolation='antialiased')

    ax.set_xlim([0., max_freq])
    ax.set_ylabel('Time (s)')
    ax.set_xlabel('Frequency (Hz)')

    # Add peaks lines in the spectrogram to get hint from peaks found in the first graph
    if peaks is not None:
        for idx, peak in enumerate(peaks):
            ax.axvline(peak, color='cyan', linestyle='dotted', linewidth=1)
            ax.annotate(f"Peak {idx+1}", (peak, bins[-1] * 0.9),
                        textcoords="data",
                        color='cyan',
                        rotation=90,
                        fontsize=10,
                        verticalalignment='top',
                        horizontalalignment='right')

    return


######################################################################
# Startup and main routines
######################################################################


def graph_resonances_test_results(csv_path: Path,
                                  max_smoothing: float | None = None,
                                  scv=5.0,
                                  max_freq=200.0):
    data = load_accelerometer_data(csv_path)

    # Compute shapers, PSD outputs and spectrogram
    performance_shaper, shapers, calibration_data, fr, zeta = calibrate_shaper(
        data, max_smoothing, scv, max_freq)
    pdata, bins, t = compute_spectrogram(data)
    del data

    # Select only the relevant part of the PSD data
    freqs = calibration_data.freq_bins
    calibration_data.psd_sum = calibration_data.psd_sum[freqs <= max_freq]
    calibration_data.psd_x = calibration_data.psd_x[freqs <= max_freq]
    calibration_data.psd_y = calibration_data.psd_y[freqs <= max_freq]
    calibration_data.psd_z = calibration_data.psd_z[freqs <= max_freq]
    calibration_data.freqs = freqs[freqs <= max_freq]

    # Peak detection algorithm
    peaks_threshold = [
        PEAKS_DETECTION_THRESHOLD * calibration_data.psd_sum.max(),
        PEAKS_EFFECT_THRESHOLD * calibration_data.psd_sum.max()
    ]
    num_peaks, peaks, peaks_freqs = detect_peaks(calibration_data.psd_sum,
                                                 calibration_data.freqs,
                                                 peaks_threshold[0])

    # Print the peaks info in the console
    peak_freqs_formated = ["{:.1f}".format(f) for f in peaks_freqs]
    num_peaks_above_effect_threshold = np.sum(
        calibration_data.psd_sum[peaks] > peaks_threshold[1])
    print(
        "\nPeaks detected on the graph: %d @ %s Hz (%d above effect threshold)"
        % (num_peaks, ", ".join(map(
            str, peak_freqs_formated)), num_peaks_above_effect_threshold))

    # Create graph layout
    fig, (ax1, ax2) = plt.subplots(2,
                                   1,
                                   gridspec_kw={
                                       'height_ratios': [4, 3],
                                       'bottom': 0.050,
                                       'top': 0.890,
                                       'left': 0.085,
                                       'right': 0.966,
                                       'hspace': 0.169,
                                       'wspace': 0.200
                                   })
    fig.set_size_inches(8.3, 11.6)

    # Add a title with some test info
    title_line1 = "INPUT SHAPER CALIBRATION TOOL"
    fig.text(0.12,
             0.965,
             title_line1,
             ha='left',
             va='bottom',
             fontsize=20,
             color=KLIPPAIN_COLORS['purple'],
             weight='bold')
    try:
        filename_parts = csv_path.name.split('_')
        dt = datetime.strptime(f"{filename_parts[1]} {filename_parts[2]}",
                               "%Y%m%d %H%M%S")
        title_line2 = dt.strftime('%x %X') + ' -- ' + filename_parts[3].upper(
        ).split('.')[0] + ' axis'
        title_line3 = '| Square corner velocity: ' + str(scv) + 'mm/s'
        title_line4 = '| Max allowed smoothing: ' + str(max_smoothing)
    except:
        print("Warning: CSV filename look to be different than expected (%s)" %
              (csv_path.name))
        title_line2 = csv_path.name
        title_line3 = ''
        title_line4 = ''
    fig.text(0.12,
             0.957,
             title_line2,
             ha='left',
             va='top',
             fontsize=16,
             color=KLIPPAIN_COLORS['dark_purple'])
    fig.text(0.58,
             0.960,
             title_line3,
             ha='left',
             va='top',
             fontsize=10,
             color=KLIPPAIN_COLORS['dark_purple'])
    fig.text(0.58,
             0.946,
             title_line4,
             ha='left',
             va='top',
             fontsize=10,
             color=KLIPPAIN_COLORS['dark_purple'])

    # Plot the graphs
    plot_freq_response(ax1, calibration_data, shapers, performance_shaper,
                       peaks, peaks_freqs, peaks_threshold, fr, zeta, max_freq)
    plot_spectrogram(ax2, t, bins, pdata, peaks_freqs, max_freq)

    return fig
