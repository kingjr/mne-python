# Author: Alexandre Gramfort <alexandre.gramfort@telecom-paristech.fr>
#         Romain Trachel <trachelr@gmail.com>
#
# License: BSD (3-clause)

import os.path as op

from nose.tools import assert_true, assert_raises, assert_equal
import numpy as np
from numpy.testing import assert_array_almost_equal, assert_array_equal

from mne import io, Epochs, read_events, pick_types
from mne.decoding.csp import CSP
from mne.utils import requires_sklearn, slow_test

data_dir = op.join(op.dirname(__file__), '..', '..', 'io', 'tests', 'data')
raw_fname = op.join(data_dir, 'test_raw.fif')
event_name = op.join(data_dir, 'test-eve.fif')

tmin, tmax = -0.2, 0.5
event_id = dict(aud_l=1, vis_l=3)
# if stop is too small pca may fail in some cases, but we're okay on this file
start, stop = 0, 8


@slow_test
def test_csp():
    """Test Common Spatial Patterns algorithm on epochs
    """
    raw = io.read_raw_fif(raw_fname, preload=False)
    events = read_events(event_name)
    picks = pick_types(raw.info, meg=True, stim=False, ecg=False,
                       eog=False, exclude='bads')
    picks = picks[2:12:3]  # subselect channels -> disable proj!
    raw.add_proj([], remove_existing=True)
    epochs = Epochs(raw, events, event_id, tmin, tmax, picks=picks,
                    baseline=(None, 0), preload=True, proj=False)
    epochs_data = epochs.get_data()
    n_channels = epochs_data.shape[1]
    y = epochs.events[:, -1]

    # Init
    assert_raises(ValueError, CSP, n_components='foo')
    for reg in ['foo', -0.1, 1.1]:
        assert_raises(ValueError, CSP, reg=reg)
    for reg in ['oas', 'ledoit_wolf', 0, 0.5, 1.]:
        CSP(reg=reg)
    for cov_est in ['foo', None]:
        assert_raises(ValueError, CSP, cov_est=cov_est)
    for cov_est in ['concat', 'epoch']:
        CSP(cov_est=cov_est)

    n_components = 3
    csp = CSP(n_components=n_components)

    # Fit
    csp.fit(epochs_data, epochs.events[:, -1])
    assert_equal(len(csp.mean_), n_components)
    assert_equal(len(csp.std_), n_components)

    # Transform
    X = csp.fit_transform(epochs_data, y)
    sources = csp.transform(epochs_data)
    assert_true(sources.shape[1] == n_components)
    assert_true(csp.filters_.shape == (n_channels, n_channels))
    assert_true(csp.patterns_.shape == (n_channels, n_channels))
    assert_array_almost_equal(sources, X)

    # Test data exception
    assert_raises(ValueError, csp.fit, epochs_data,
                  np.zeros_like(epochs.events))
    assert_raises(ValueError, csp.fit, epochs, y)
    assert_raises(ValueError, csp.transform, epochs)

    # Test plots
    epochs.pick_types(meg='mag')
    cmap = ('RdBu', True)
    components = np.arange(n_components)
    for plot in (csp.plot_patterns, csp.plot_filters):
        plot(epochs.info, components=components, res=12, show=False, cmap=cmap)

    # Test covariance estimation methods (results should be roughly equal)
    np.random.seed(0)
    csp_epochs = CSP(cov_est="epoch")
    csp_epochs.fit(epochs_data, y)
    for attr in ('filters_', 'patterns_'):
        corr = np.corrcoef(getattr(csp, attr).ravel(),
                           getattr(csp_epochs, attr).ravel())[0, 1]
        assert_true(corr >= 0.94)

    # Test with more than 2 classes
    epochs = Epochs(raw, events, tmin=tmin, tmax=tmax, picks=picks,
                    event_id=dict(aud_l=1, aud_r=2, vis_l=3, vis_r=4),
                    baseline=(None, 0), proj=False, preload=True)
    epochs_data = epochs.get_data()
    n_channels = epochs_data.shape[1]

    n_channels = epochs_data.shape[1]
    for cov_est in ['concat', 'epoch']:
        csp = CSP(n_components=n_components, cov_est=cov_est)
        csp.fit(epochs_data, epochs.events[:, 2]).transform(epochs_data)
        assert_equal(len(csp._classes), 4)
        assert_array_equal(csp.filters_.shape, [n_channels, n_channels])
        assert_array_equal(csp.patterns_.shape, [n_channels, n_channels])


@requires_sklearn
def test_regularized_csp():
    """Test Common Spatial Patterns algorithm using regularized covariance
    """
    raw = io.read_raw_fif(raw_fname, preload=False)
    events = read_events(event_name)
    picks = pick_types(raw.info, meg=True, stim=False, ecg=False,
                       eog=False, exclude='bads')
    picks = picks[1:13:3]
    epochs = Epochs(raw, events, event_id, tmin, tmax, picks=picks,
                    baseline=(None, 0), preload=True)
    epochs_data = epochs.get_data()
    n_channels = epochs_data.shape[1]

    n_components = 3
    reg_cov = [None, 0.05, 'ledoit_wolf', 'oas']
    for reg in reg_cov:
        csp = CSP(n_components=n_components, reg=reg)
        csp.fit(epochs_data, epochs.events[:, -1])
        y = epochs.events[:, -1]
        X = csp.fit_transform(epochs_data, y)
        assert_true(csp.filters_.shape == (n_channels, n_channels))
        assert_true(csp.patterns_.shape == (n_channels, n_channels))
        assert_array_almost_equal(csp.fit(epochs_data, y).
                                  transform(epochs_data), X)

        # test init exception
        assert_raises(ValueError, csp.fit, epochs_data,
                      np.zeros_like(epochs.events))
        assert_raises(ValueError, csp.fit, epochs, y)
        assert_raises(ValueError, csp.transform, epochs)

        csp.n_components = n_components
        sources = csp.transform(epochs_data)
        assert_true(sources.shape[1] == n_components)


@requires_sklearn
def test_csp_pipeline():
    """Test if CSP works in a pipeline
    """
    from sklearn.svm import SVC
    from sklearn.pipeline import Pipeline
    csp = CSP(reg=1)
    svc = SVC()
    pipe = Pipeline([("CSP", csp), ("SVC", svc)])
    pipe.set_params(CSP__reg=0.2)
    assert_true(pipe.get_params()["CSP__reg"] == 0.2)
