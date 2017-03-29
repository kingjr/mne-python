"""
=========================================================================
Decoding sensor space data with generalization across time and conditions
=========================================================================

This example runs the analysis described in [1]_. It illustrates how one can
fit a linear classifier to identify a discriminatory topography at a given time
instant and subsequently assess whether this linear model can accurately
predict all of the time samples of a second set of conditions.

References
----------

.. [1] King & Dehaene (2014) 'Characterizing the dynamics of mental
       representations: the temporal generalization method', Trends In
       Cognitive Sciences, 18(4), 203-210. doi: 10.1016/j.tics.2014.01.002.
"""
# Authors: Jean-Remi King <jeanremi.king@gmail.com>
#          Alexandre Gramfort <alexandre.gramfort@telecom-paristech.fr>
#          Denis Engemann <denis.engemann@gmail.com>
#
# License: BSD (3-clause)

import matplotlib.pyplot as plt

from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

import mne
from mne.datasets import sample
from mne.decoding import GeneralizingEstimator

print(__doc__)

# Preprocess data
data_path = sample.data_path()
# Load and filter data, set up epochs
raw_fname = data_path + '/MEG/sample/sample_audvis_filt-0-40_raw.fif'
events_fname = data_path + '/MEG/sample/sample_audvis_filt-0-40_raw-eve.fif'
raw = mne.io.read_raw_fif(raw_fname, preload=True)
picks = mne.pick_types(raw.info, meg=True, exclude='bads')  # Pick MEG channels
raw.filter(1, 30, method='fft')  # Band pass filtering signals
events = mne.read_events(events_fname)
event_id = {'Auditory/Left': 1, 'Auditory/Right': 2,
            'Visual/Left': 3, 'Visual/Right': 4}
tmin = -0.050
tmax = 0.400
decim = 2  # decimate to make the example faster to run
epochs = mne.Epochs(raw, events, event_id=event_id, tmin=tmin, tmax=tmax,
                    proj=True, picks=picks, baseline=None, preload=True,
                    reject=dict(mag=5e-12), decim=decim)

epochs_left = epochs['Left']
epochs_right = epochs['Right']

# We will train the classifier on all left visual vs auditory trials
# and test on all right visual vs auditory trials.
clf = make_pipeline(StandardScaler(), LogisticRegression())
tg = GeneralizingEstimator(clf, scoring='roc_auc', n_jobs=1)

# Encode the y with similar values in both cases
y_left = epochs_left.events[:, 2] > 2
y_right = epochs_right.events[:, 2] > 2
tg.fit(X=epochs_left.get_data(), y=y_left)
scores = tg.score(X=epochs_right.get_data(), y=y_right)

# plot
fig, ax = plt.subplots(1)
im = ax.matshow(scores, vmin=0, vmax=1., cmap='RdBu_r', origin='lower',
                extent=[epochs_left.times.min(), epochs_left.times.max()] * 2)
ax.axhline(0., color='k')
ax.axvline(0., color='k')
ax.xaxis.set_ticks_position('bottom')
ax.set_xlabel('Testing Time (s)')
ax.set_ylabel('Training Time (s)')
ax.set_title('Generalization across time and condition')
plt.colorbar(im, ax=ax)
plt.show()
