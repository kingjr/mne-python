# Author: Jean-Remi King <jeanremi.king@gmail.com>
#
# License: BSD (3-clause)

import numpy as np
from .mixin import TransformerMixin
from .base import BaseEstimator
from ..time_frequency.tfr import _compute_tfr, _check_tfr_param


class TimeFrequency(TransformerMixin, BaseEstimator):
    """Time frequency transformer.

    Time-frequency transform of times series along the last axis.

    Parameters
    ----------
    frequencies : array-like of floats, shape (n_freqs,)
        The frequencies.
    sfreq : float | int, defaults to 1.0
        Sampling frequency of the data.
    method : 'multitaper' | 'morlet', defaults to 'morlet'
        The time-frequency method. 'morlet' convolves a Morlet wavelet.
        'multitaper' uses Morlet wavelets windowed with multiple DPSS
        multitapers.
    n_cycles : float | array of float, defaults to 7.0
        Number of cycles  in the Morlet wavelet. Fixed number
        or one per frequency.
    time_bandwidth : float, defaults to None
        If None and method=multitaper, will be set to 4.0 (3 tapers).
        Time x (Full) Bandwidth product. Only applies if
        method == 'multitaper'. The number of good tapers (low-bias) is
        chosen automatically based on this to equal floor(time_bandwidth - 1).
    use_fft : bool, defaults to True
        Use the FFT for convolutions or not.
    decim : int | slice, defaults to 1
        To reduce memory usage, decimation factor after time-frequency
        decomposition.
        If `int`, returns tfr[..., ::decim].
        If `slice`, returns tfr[..., decim].
        .. note:
            Decimation may create aliasing artifacts, yet decimation
            is done after the convolutions.
    output : str, defaults to 'complex'

        * 'complex' : single trial complex.
        * 'power' : single trial power.
        * 'phase' : single trial phase.

    n_jobs : int, defaults to 1
        The number of epochs to process at the same time. The parallelization
        is implemented across channels.
    verbose : bool, str, int, or None, defaults to None
        If not None, override default verbose level (see mne.verbose).

    See Also
    --------
    mne.time_frequency.single_trial_power
    mne.time_frequency.tfr_morlet
    mne.time_frequency.tfr_multitaper
    """

    def __init__(self, frequencies, sfreq=1.0, method='morlet', n_cycles=7.0,
                 time_bandwidth=None, use_fft=True, decim=1, output='complex',
                 n_jobs=1, verbose=None):
        """Init TimeFrequency transformer."""
        frequencies, sfreq, _, n_cycles, time_bandwidth, decim = \
            _check_tfr_param(frequencies, sfreq, method, True, n_cycles,
                             time_bandwidth, use_fft, decim, output)
        self.frequencies = frequencies
        self.sfreq = sfreq
        self.method = method
        self.n_cycles = n_cycles
        self.time_bandwidth = time_bandwidth
        self.use_fft = use_fft
        self.decim = decim
        # Check that output is not an average metric (e.g. ITC)
        if output not in ['complex', 'power', 'phase']:
            raise ValueError("output must be 'complex', 'power', 'phase'.")
        self.output = output
        self.n_jobs = n_jobs
        self.verbose = verbose

    def fit_transform(self, X, y=None):
        """
        Time-frequency transform of times series along the last axis.

        Parameters
        ----------
        X : array, shape (n_samples, n_channels, n_times)
            The training data samples. The channel dimension can be zero- or
            n-dimensional.
        y : None
            For scikit-learn compatibility purposes.

        Returns
        -------
        Xt : array, shape (n_samples, n_channels, n_frequencies, n_times)
            The time-frequency transform of the data, where n_channels can be
            zero- or n-dimensional.
        """
        return self.fit(X, y).transform(X)

    def fit(self, X, y=None):
        """ Does nothing, for scikit-learn compatibility purposes.

        Parameters
        ----------
        X : array, shape (n_samples, n_channels, n_times)
            The training data.
        y : array | None
            The target values.

        Returns
        -------
        self : object
            Return self.
        """
        return self

    def transform(self, X):
        """Time-frequency transform of times series along the last axis.

        Parameters
        ----------
        X : array, shape (n_samples, n_channels, n_times)
            The training data samples. The channel dimension can be zero- or
            n-dimensional.

        Returns
        -------
        Xt : array, shape (n_samples, n_channels, n_frequencies, n_times)
            The time-frequency transform of the data, where n_channels can be
            zero- or n-dimensional.

        """
        # Ensure 3-dimensional X
        n_samples, n_times = len(X), X.shape[-1]
        shape = X.shape[1:-1]
        if not shape:
            X = X[:, np.newaxis, :]
        elif len(shape) > 1:
            X = X.reshape(n_samples, -1, n_times)

        # Compute time-frequency
        Xt = _compute_tfr(X, self.frequencies, self.sfreq, self.method,
                          self.n_cycles, True, self.time_bandwidth,
                          self.use_fft, self.decim, self.output, self.n_jobs,
                          self.verbose)

        # Back to original shape
        if not shape:
            Xt = Xt[:, 0, :]
        elif len(shape) > 1:
            # n_time must be -1 because of decim parameter
            shape = np.r_[n_samples, shape, len(self.frequencies), -1]
            Xt = Xt.reshape(shape)
        return Xt
