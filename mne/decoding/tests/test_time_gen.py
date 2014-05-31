# Author: Alexandre Gramfort <alexandre.gramfort@telecom-paristech.fr>
#
# License: BSD (3-clause)

import warnings
import os.path as op

from nose.tools import assert_true

from mne import io, Epochs, read_events, pick_types
from mne.utils import _TempDir, requires_sklearn, create_slices
from mne.decoding import time_generalization
from mne.fixes import partial
import time


tempdir = _TempDir()

data_dir = op.join(op.dirname(__file__), '..', '..', 'io', 'tests', 'data')
raw_fname = op.join(data_dir, 'test_raw.fif')
event_name = op.join(data_dir, 'test-eve.fif')

tmin, tmax = -0.2, 0.5
event_id = dict(aud_l=1, vis_l=3)
event_id_gen = dict(aud_l=2, vis_l=4)


@requires_sklearn
def test_time_generalization():
    """Test time generalization decoding
    """
    raw = io.Raw(raw_fname, preload=False)
    events = read_events(event_name)
    picks = pick_types(raw.info, meg='mag', stim=False, ecg=False,
                       eog=False, exclude='bads')
    picks = picks[1:13:3]
    decim = 30

    with warnings.catch_warnings(record=True) as w:
        # Test on time generalization within one condition
        epochs = Epochs(raw, events, event_id, tmin, tmax, picks=picks,
                        baseline=(None, 0), preload=True, decim=decim)

        epochs_list = [epochs[k] for k in event_id.keys()]
        results = time_generalization(epochs_list, cv=2, random_state=42)
        scores = results['scores']
        n_slices = len(epochs.times)
        # Test that by default, the temporal generalization is trained and
        # Tested across all time points
        assert_true(scores.shape == (n_slices, n_slices))
        # Test that the decoding scores are between 0 and 1
        assert_true(scores.max() <= 1.)
        assert_true(scores.min() >= 0.)
        # Test that traing and testing time are correct for asymetrical 
        # training and testing times
        n_slices = len(epochs_list[0].times)
        train_slices = create_slices(n_slices, across_step=2)
        test_slices = [create_slices(n_slices, within_step=2)] * \
                      len(train_slices)
        results = time_generalization(epochs_list, cv=2, random_state=42,
                                      train_slices=train_slices,
                                      test_slices=test_slices)
        scores = results['scores']
        assert_true(scores.shape == (8, 15))
        # Test create_slice callable
        train_slices = partial(create_slices,across_step=2)
        results = time_generalization(epochs_list, cv=2, random_state=42,
                                      train_slices=train_slices)
        # Test on time generalization within across two conditions
        epochs_list_gen = Epochs(raw, events, event_id_gen, tmin, tmax, 
                                 picks=picks, baseline=(None, 0),
                                 preload=True, decim=decim)
        epochs_list_gen = [epochs_list_gen[k] for k in event_id.keys()]
        results = time_generalization(epochs_list, 
                                      epochs_list_gen=epochs_list_gen,
                                      cv=2, random_state=42)
        scores = results['scores']
        scores_gen = results['scores_gen']
        assert_true(scores.shape == scores_gen.shape)
        assert_true(scores_gen.max() <= 1.)
        assert_true(scores_gen.min() >= 0.)

        # Test parallelization & timing
        r = time_generalization(epochs_list, parallel_across='time_samples')
        r = time_generalization(epochs_list, parallel_across='folds')
        
        if False:
            # With more computing & memory load
            picks = pick_types(raw.info, meg='grad', stim=False, ecg=False,
                               eog=False, exclude='bads')
            epochs = Epochs(raw, events, event_id, -.5, 1.5, picks=picks,
                            baseline=(None, 0), preload=True)
            epochs_list = [epochs[k] for k in event_id.keys()]

            def timeit(slices, cv, parallel):
                test_slices = [create_slices(len(slices), across_step=1, 
                                             width=slices[0].stop - \
                                             slices[0].start)] * \
                              len(slices)
                t = time.time()
                results = time_generalization(epochs_list, train_slices=slices,
                                              test_slices=test_slices, 
                                              generalization='cardinal', 
                                              n_jobs=-1, 
                                              parallel_across=parallel, cv=cv)
                print(time.time() - t)
                return results

            slices = create_slices(200, width=50, across_step=1)
            results = timeit(slices, 2, 'folds')
            results = timeit(slices, 2, 'time_samples')

            slices = create_slices(20, start=10)
            results = timeit(slices, 6, 'folds')
            results = timeit(slices, 6, 'time_samples')
        
