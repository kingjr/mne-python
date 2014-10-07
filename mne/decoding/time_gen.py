# Authors: Jean-Remi King <jeanremi.king@gmail.com>
#          Alexandre Gramfort <alexandre.gramfort@telecom-paristech.fr>
#          Denis Engemann <denis.engemann@gmail.com>
#
# License: BSD (3-clause)

import numpy as np
from scipy.stats import mode
import matplotlib.pyplot as plt

from mne.parallel import parallel_func
from mne import pick_types
from ..utils import logger, verbose, deprecated


class GeneralizationAcrossTime(object):

    """Create object used to 1) fit a series of classifiers on 
    multidimensional time-resolved data, and 2) test the ability of each 
    classifier to generalize across other time samples.


    Parameters
    ----------
    clf : object | None
        A object Scikit-Learn estimator API (fit & predict).
        If None the classifier will be a standard pipeline:
        (scaler, linear SVM (C=1.)).
    cv : int | object, optional, default: 5
        If an integer is passed, it is the number of fold (default 5).
        Specific cross-validation objects can be passed, see
        sklearn.cross_validation module for the list of possible objects.
    picks : array (n_selected_chans) | None, optional, default: None
        Channels to be included in Sklearn model fitting.
    train_times : dict, optional, default: {} 
        'slices' : array, shape(n_clfs)
            Array of time slices (in indices) used for each classifier.
        'start' : float
            Time at which to start decoding (in seconds). By default, 
            min(epochs.times).
        'stop' : float
            Maximal time at which to stop decoding (in seconds). By default, 
            max(times).
        'step' : float
            Duration separating the start of to subsequent classifiers (in 
            seconds). By default, equals one time sample.
        'length' : float
            Duration of each classifier (in seconds). By default, equals one 
            time sample.

    Returns
    -------
    gat : object
        gat.fit() is used to train classifiers
        gat.predict() is used to test the classifiers on existing or novel data

    Notes
    -----
    The function implements the method used in:

    Jean-Remi King, Alexandre Gramfort, Aaron Schurger, Lionel Naccache
    and Stanislas Dehaene, "Two distinct dynamic modes subtend the detection of
    unexpected sounds", PLOS ONE, 2013
    """

    def __init__(self, cv=5, clf=None,
                 picks=None,
                 train_times={}):

        from sklearn.preprocessing import StandardScaler
        from sklearn.svm import SVC
        from sklearn.pipeline import Pipeline

        # Store parameters in object
        self.cv = cv
        self.clf = clf
        self.picks = picks
        self.train_times = train_times
        self.picks = picks

        # Default classification pipeline
        if clf is None:
            scaler = StandardScaler()
            svc = SVC(C=1, kernel='linear')
            clf = Pipeline([('scaler', scaler), ('svc', svc)])
        # clf = SVC(C=1, kernel='linear')  # XXX remove
        self.clf = clf

    def fit(self, epochs, y=None, n_jobs=1):
        """ Train a classifier on each specified time slice.

        Parameters
        ----------
        epochs : instance of Epochs
            The epochs.
        y : array | None, optional, default: None
            To-be-fitted model values. If None, y = [epochs.events[:,2]]
        n_jobs : int, optional, default: 1
            Number of jobs to run in parallel.
        """
        from sklearn.cross_validation import StratifiedKFold
        from sklearn.base import clone
        from sklearn.cross_validation import check_cv

        # Default channel selection
        # XXX Channel selection should be transformed into a make_chans_pick,
        # and defined in __init__
        if self.picks is None:
            info = epochs.info
            self.picks = pick_types(
                info, meg=True, eeg=True, exclude='bads')

        # Extract data from MNE structure
        X, y = _format_data(epochs, y, self.picks)
        self.y_train = y

        # Cross validation scheme
        # XXX Cross validation should be transformed into a make_cv, and
        # defined in __init__
        if self.cv.__class__ == int:
            self.cv = StratifiedKFold(y, self.cv)
        self.cv = check_cv(self.cv, X, y, classifier=True)

        # Define training sliding window
        self.train_times['slices'] = _sliding_window(
            epochs.times, self.train_times)

        # Keep last training times in milliseconds
        self.train_times['s'] = epochs.times[[t[-1]
                                              for t in self.train_times['slices']]]

        # Chunk X for parallelization
        if n_jobs > 0:
            n_chunk = n_jobs
        else:
            import multiprocessing
            n_chunk = multiprocessing.cpu_count()
        # Parallel across training time
        parallel, p_time_gen, _ = parallel_func(_fit_slices, n_jobs)
        packed = parallel(p_time_gen(clone(self.clf),
                                     X[:, :, np.unique(
                                         np.concatenate(train_slices_chunk))],
                                     y,
                                     train_slices_chunk,
                                     self.cv)
                          for train_slices_chunk in np.array_split(
            self.train_times['slices'], n_chunk))
        # Unpack estimators
        unpacked = []
        for ii in range(len(packed)):
            for jj in range(len(packed[ii])):
                unpacked += [packed[ii][jj]]
        self.estimators = unpacked

    def predict(self, epochs, independent=False, test_times=None,
                predict_type='predict', n_jobs=1):
        """ Test each classifier on each specified testing time slice.

        Parameters
        ----------
        epochs : instance of Epochs
            The epochs. Can be similar to fitted epochs or not. See independent
            parameter.
        independent : bool
            Indicates whether data X is independent from the data used to fit 
            the  classifier. If independent == True, the predictions from each
            cv fold classifier are averaged. Else, only the prediction from the
            corresponding fold is used.
        test_times : str | dict | None, optional, default: None 
            if test_times = 'diagonal', test_times = train_times: decode at 
            each time point but does not generalize.
            'slices' : array, shape(n_clfs)
                Array of time slices (in indices) used for each classifier.
            'start' : float
                Time at which to start decoding (in seconds). By default, 
                min(epochs.times).
            'stop' : float
                Maximal time at which to stop decoding (in seconds). By
                default, max(times).
            'step' : float
                Duration separating the start of to subsequent classifiers (in 
                seconds). By default, equals one time sample.
            'length' : float
                Duration of each classifier (in seconds). By default, equals 
                one time sample.
        n_jobs : int
            Number of jobs to run in parallel. Each fold is fit
            in parallel.

        Returns
        -------
        self.y_pred : array, shape(n_train_time, n_test_time, n_trials,
                                   n_prediction_dim)
        """

        X, y = _format_data(epochs, None, self.picks)
        
        # Check that at least one classifier has been trained
        assert(hasattr(self, 'estimators'))

        # Cross validation scheme: if same data set use CV for prediction, else
        # predict each trial with all folds' classifiers
        self.independent = independent  # XXX Good name?

        # Store y for scorer
        if not independent:
            self.y_true = self.y_train  # 'y_true': Good name?
        else:
            self.y_true = y

        # Store type of prediction (continuous, categorical etc)
        self.predict_type = predict_type

        # Define testing sliding window
        if test_times == 'diagonal':
            test_times = {}
            test_times['slices'] = [[s] for s in self.train_times['slices']]
        elif test_times is None:
            test_times = {}
        if not 'slices' in test_times:
            # Initialize array
            test_times['slices_'] = []
            # Force same number of time sample in testing than in training
            # (otherwise it won 't be the same number of features')
            test_times['length'] = self.train_times['length']
            # Make a sliding window for each training time.
            for t in range(0, len(self.train_times['slices'])):
                test_times['slices_'] += [
                    _sliding_window(epochs.times, test_times)]
            test_times['slices'] = test_times['slices_']
            del test_times['slices_']

        # Testing times in milliseconds (only keep last time if multiple time
        # slices)
        test_times['s'] = [[epochs.times[t_test[-1]] for t_test in t_train]
                           for t_train in test_times['slices']]
        # Store all testing times parameters
        self.test_times = test_times

        # Prepare parallel predictions
        parallel, p_time_gen, _ = parallel_func(_predict_time_loop, n_jobs)

        # Initialize results
        self.y_pred = [[]] * len(test_times['slices'])

        # Loop across estimators (i.e. training times)
        packed = parallel(p_time_gen(
            X,
            self.estimators[t_train],
            self.cv,
            slices,
            self.independent, self.predict_type)
            for t_train, slices in enumerate(test_times['slices']))

        self.y_pred = np.transpose(zip(*packed), (1, 0, 2, 3))

    def score(self, epochs, y=None, scorer=None, independent=False, test_times=None,
                predict_type='predict', n_jobs=1):
        """ Aux function of GeneralizationAcrossTime
        Estimate score across trials by comparing the prediction estimated for
        each trial to its true value.

        Parameters
        ----------
        epochs : instance of Epochs
            The epochs. Can be similar to fitted epochs or not. See independent
            parameter.
        y : list | array, shape (n_trials) | None, optional, default: None
            To-be-fitted model, If None, y = [epochs.events[:,2]]
        scorer : object
            Sklearn scoring object
        independent : bool
            Indicates whether data X is independent from the data used to fit 
            the  classifier. If independent == True, the predictions from each
            cv fold classifier are averaged. Else, only the prediction from the
            corresponding fold is used.
        test_times : str | dict | None, optional, default: None
            if test_times = 'diagonal', test_times = train_times: decode at 
            each time point but does not generalize.
            'slices' : array, shape(n_clfs)
                Array of time slices (in indices) used for each classifier.
            'start' : float
                Time at which to start decoding (in seconds). By default, 
                min(epochs.times).
            'stop' : float
                Maximal time at which to stop decoding (in seconds). By
                default, max(times).
            'step' : float
                Duration separating the start of to subsequent classifiers (in 
                seconds). By default, equals one time sample.
            'length' : float
                Duration of each classifier (in seconds). By default, equals 
                one time sample.
        n_jobs : int
            Number of jobs to run in parallel. Each fold is fit
            in parallel.
        
        Returns
        -------
        self.y_pred : array, shape(n_train_time, n_test_time, n_trials,
                                   n_prediction_dim)
        self.scores : array, shape(n_slices)
            Score estimated across all trials for each train/tested time slice.

        """

        from sklearn.metrics import roc_auc_score, accuracy_score

        # Run predictions
        self.predict(epochs, independent=independent, 
                test_times=test_times, predict_type=predict_type, 
                n_jobs=n_jobs)
        
        # If no regressor is passed, use default epochs events
        if y is None:
            y = self.y_true  # XXX good name?
        self.y_true = y  # true regressor to be compared with y_pred

        # Setup scorer
        if scorer is None:
            if self.predict_type == 'predict':
                scorer = accuracy_score
            else:
                scorer = roc_auc_score
        self.scorer = scorer

        # Initialize values: Note that this is not an array as the testing
        # times per training time need not be regular
        scores = [[]] * len(self.test_times['slices'])

        # Loop across training/testing times
        for t_train, slices in enumerate(self.test_times['slices']):
            n_time = len(slices)
            # Loop across testing times
            scores[t_train] = [0] * n_time
            for t, indices in enumerate(slices):
                # Scores across trials
                scores[t_train][t] = _scorer(self.y_true,
                                             self.y_pred[t_train][t],
                                             scorer)
        self.scores = scores


def _predict_time_loop(X, estimator, cv, slices, independent,
                       predict_type):
    """Aux function of GeneralizationAcrossTime

    Run classifiers predictions loop across time samples.

    Parameters
    ----------
    X : array, shape (n_trials, n_features, n_times)
        To-be-fitted data
    estimators : array, shape(n_folds)
        Array of Sklearn classifiers fitted in cross-validation.
    slices : list, shape(n_slices)
        List of slices selecting data from X from which is prediction is 
        generated.
    independent : bool
        Indicates whether data X is independent from the data used to fit the 
        classifier. If independent == True, the predictions from each cv fold
        classifier are averaged. Else, only the prediction from the 
        corresponding fold is used.
    predict_type : str
        Indicates the type of prediction ('predict', 'proba', 'distance').

    Returns
    -------
    y_pred : array, shape(n_slices, n_trials)
        Single trial prediction for each train/tested time sample.

    """
    n_trial = len(X)
    n_time = len(slices)
    # Loop across testing slices
    y_pred = [[]] * n_time
    for t, indices in enumerate(slices):
        # Flatten features in case of multiple time samples
        Xtrain = X[:, :, indices].reshape(
            n_trial, np.prod(X[:, :, indices].shape[1:]))

        # Single trial predictions
        if not independent:
            # If predict within cross validation, only predict with
            # corresponding classifier, else predict with each fold's
            # classifier and average prediction.
            for k, [train, test] in enumerate(cv):
                # XXX I didn't manage to initalize correctly this array, as
                # its size depends on the the type of predicter and the
                # number of class.
                if k == 0:
                    y_pred_ = _predicter(Xtrain[test, :],
                                         [estimator[k]],
                                         predict_type)
                    y_pred[t] = np.empty((n_trial, y_pred_.shape[1]))
                    y_pred[t][test, :] = y_pred_
                y_pred[t][test, :] = _predicter(Xtrain[test, :],
                                                [estimator[k]],
                                                predict_type)
        else:
            y_pred[t] = _predicter(Xtrain, estimator, predict_type)
    return y_pred


def _scorer(y, y_pred, scorer):
    """Aux function of GeneralizationAcrossTime

    Estimate classifiaction score.

    Parameters
    ----------
    y : list | array, shape (n_trials)
        True model value
    y_pred : list | array, shape (n_trials)
        Classifier prediction of model value
    scorer : object
        Sklearn scoring object

    Returns
    -------
    score : float
        Score estimated across all trials for each train/tested time sample.
    y_pred : array, shape(n_slices, n_trials)
        Single trial prediction for each train/tested time sample.

    """
    classes = np.unique(y)
    # if binary prediction or discrete prediction
    if y_pred.shape[1] == 1:
        # XXX Problem here with scorer when proba=True but y !=  (0 | 1)
        try:
            score = scorer(y, y_pred)
        except:
            score = scorer(y == max(classes), y_pred)
    else:
        # This part is not sufficiently generic to apply to all classification
        # and regression cases.
        score = 0
        for ii, c in enumerate(classes):
            score += scorer(y == c, y_pred[:, ii])
        score /= len(classes)
    return score


def _format_data(epochs, y, picks):
    """Aux function of GeneralizationAcrossTime

    Format MNE data into Sklearn X and y

    Parameters
    ----------
    epochs : instance of Epochs
            The epochs.
    y : array shape(n_trials) | list shape(n_trials) | None
        To-be-fitted model. If y is None, y = epochs.events
    picks : array (n_selected_chans) | None
        Channels to be included in Sklearn model fitting.

    Returns
    -------
    X : array, shape(n_trials, n_selected_chans, n_times)
        To-be-fitted data
    y : array, shape(n_trials)
        To-be-fitted model
    picks : array, shape()
    """
    # If no regressor is passed, use default epochs events
    if y is None:
        y = epochs.events[:, 2]
    # Convert MNE data into trials x features x time matrix
    X = epochs.get_data()[:, picks, :]
    # Check data sets
    assert(X.shape[0] == y.shape[0])
    return X, y


def _fit_slices(clf, Xchunk, y, slices, cv):
    """Aux function of GeneralizationAcrossTime

    Fit each classifier.

    Parameters
    ----------
    clf : Sklearn classifier
    Xchunk : array, shape (n_trials, n_features, n_times)
        To-be-fitted data
    y : list | array, shape (n_trials)
        To-be-fitted model
    slices : list | array, shape(n_training_slice)
        List of training slices, indicating time sample relative to X
    cv : Sklearn cross-validater

    Returns
    -------
    estimators : list
        List of fitted Sklearn classifiers corresponding to each training slice
    """
    from sklearn.base import clone
    # Initialize
    n_trials = len(Xchunk)
    estimators = []
    # Identify the time samples of X_chunck corresponding to X
    values = np.unique(np.concatenate(slices))
    indices = range(len(values))
    # Loop across time slices
    for t_slice in slices:
        # Translate absolute time samples into time sample relative to Xchunk
        for ii in indices:
            t_slice[t_slice == values[ii]] = indices[ii]
        # Select slice
        X = Xchunk[:, :, t_slice]
        # Reshape data matrix to flatten features in case of multiple time
        # samples.
        X = X.reshape(n_trials, np.prod(X.shape[1:]))
        # Loop across folds
        estimators_ = []
        for fold, (train, test) in enumerate(cv):
            # Fit classifier
            clf_ = clone(clf)
            clf_.fit(X[train, :], y[train])
            estimators_ += [clf_]
        # Store classifier
        estimators += [estimators_]
    return estimators


def _sliding_window(times, options):
    """Aux function of GeneralizationAcrossTime

    Define the slices on which to train each classifier.

    Parameters
    ----------
    times : array, shape (n_times)
        Array of times from MNE epochs
    options : dict, optional keys: ('start', 'stop', 'step', 'length' )
        'start' : float
            Minimum time at which to stop decoding (in seconds). By default, 
            max(times).
        'stop' : float
            Maximal time at which to stop decoding (in seconds). By default, 
            max(times).
        'step' : float
            Duration separating the start of to subsequent classifiers (in 
            seconds). By default, equals one time sample.
        'length' : float
            Duration of each classifier (in seconds). By default, equals one 
            time sample.

    Returns
    -------
    time_pick : list, shape(n_classifiers)
        List of training slices, indicating for each classifier the time sample 
        (in indices of times) to be fitted on.
    """

    # Sampling frequency
    freq = (times[-1] - times[0]) / len(times)

    # Default values
    if ('slices' in options) and np.all([key in options
                                         for key in ('start', 'stop', 'step', 'length')]):
        time_pick = options['slices']
    else:
        if not 'start' in options:
            options['start'] = times[0]
        if not 'stop' in options:
            options['stop'] = times[-1]
        if not 'step' in options:
            options['step'] = freq
        if not 'length' in options:
            options['length'] = freq

        # Convert seconds to index

        def find_time(t):
            if any(times >= t):
                return np.nonzero(times >= t)[0][0]
            else:
                print('Timing outside limits!')
                raise

        start = find_time(options['start'])
        stop = find_time(options['stop'])
        step = int(round(options['step'] / freq))
        length = int(round(options['length'] / freq))

        # For each training slice, give time samples to be included
        time_pick = [range(start, start + length)]
        while (time_pick[-1][0] + step) <= (stop - length + 1):
            start = time_pick[-1][0] + step
            time_pick += [range(start, start + length)]

    return time_pick


def _predicter(X, estimators, predict_type):
    """Aux function of GeneralizationAcrossTime

    Predict each classifier. If multiple classifiers are passed, average 
    prediction across all classifier to result in a single prediction per 
    classifier.

    Parameters
    ----------
    estimators : array, shape(n_folds) or shape(1)
        Array of Sklearn classifiers to predict data
    X : array, shape (n_trials, n_features, n_times)
        To-be-predicted data
    predict_type : str, 'predict' | 'distance' | 'proba'
        'predict' : simple prediction of y (e.g. SVC, SVR)
        'distance': continuous prediction (e.g. decision_function)
        'proba': probabilistic prediction (e.g. SVC(probability=True))

    Returns
    -------
    y_pred : array, shape(n_trials, m_prediction_dimensions)
        Classifier's prediction for each trial.
    """
    # Initialize results:
    # XXX Here I did not manage to find an efficient and generic way to guess
    # the number of output provided by predict, and could thus not initalize
    # the y_pred values.
    n_trial = X.shape[0]
    n_clf = len(estimators)
    if predict_type == 'predict':
        n_class = 1
    elif predict_type == 'distance':
        n_class = estimators[0].decision_function(X[0, :]).shape[1]
    elif predict_type == 'proba':
        n_class = estimators[0].predict_proba(X[0, :]).shape[1]
    y_pred = np.ones((n_trial, n_class, n_clf))

    # Compute prediction for each sub-estimator (i.e. per fold)
    # if independent, estimators = all folds
    for fold, clf in enumerate(estimators):
        if predict_type == 'predict':
            # Discrete categorical prediction
            y_pred[:, 0, fold] = clf.predict(X)
        elif predict_type == 'proba':
            # Probabilistic prediction
            y_pred[:, :, fold] = clf.predict_proba(X)
        elif predict_type == 'distance':
            # Continuous non-probabilistic predict
            y_pred[:, :, fold] = clf.decision_function(X)

    # Collapse y_pred across folds if necessary (i.e. if independent)
    if fold > 0:
        if predict_type == 'predict':
            y_pred, _ = mode(y_pred, axis=2)
        else:
            y_pred = np.mean(y_pred, axis=2)

    # Remove unnecessary symetrical prediction (i.e. for probas & distances)
    if predict_type != 'predict' and y_pred.shape[1] == 2:
        y_pred = y_pred[:, 1, :]
        n_class = 1

    # Format shape
    y_pred = y_pred.reshape((n_trial, n_class))
    return y_pred


def plot_gat(gat, title=None, vmin=0., vmax=1., tlim=None, ax=None,
             show=True):
    """Plotting function of GeneralizationAcrossTime object

    Predict each classifier. If multiple classifiers are passed, average 
    prediction across all classifier to result in a single prediction per 
    classifier.

    Parameters
    ----------
    gat : object
        GeneralizationAcrossTime object containing predictions.
    title : str | None, optional, default : None
        Figure title.
    vmin : float, optional, default:0.
        Min color value for score.
    vmax : float, optional, default:1.
        Max color value for score.
    tlim : array, (train_min_max, test_min_max) | None, optional, 
        default: None
    ax : object | None, optional, default: None
        Plot pointer. If None, generate new figure.
    show : bool, optional, default: True
        plt.show()       

    Returns
    -------
    ax : object
        Plot pointer.
    """

    # Check that same amount of testing time per training time
    assert(len(np.unique([len(t) for t in gat.test_times])))
    # Setup plot
    if ax is None:
        fig, ax = plt.subplots(1, 1)

    # Define time limits
    if tlim is None:
        tlim = [gat.test_times['s'][0][0], gat.test_times['s'][-1][-1],
                gat.train_times['s'][0], gat.train_times['s'][-1]]
    # Plot scores
    im = ax.imshow(gat.scores, interpolation='nearest', origin='lower',
                   extent=tlim, vmin=vmin, vmax=vmax)
    ax.set_xlabel('Testing Time (s)')
    ax.set_ylabel('Training Time (s)')
    if not title is None:
        ax.set_title(title)
    ax.axvline(0, color='k')
    ax.axhline(0, color='k')
    plt.colorbar(im, ax=ax)
    if show:
        plt.show()
    return im, ax


def plot_decod(gat, title=None, ymin=0., ymax=1., ax=None, show=True,
               color='b'):
    """Plotting function of GeneralizationAcrossTime object

    Predict each classifier. If multiple classifiers are passed, average 
    prediction across all classifier to result in a single prediction per 
    classifier.

    Parameters
    ----------
    gat : object
        GeneralizationAcrossTime object containing predictions.
    title : str | None, optional, default : None
        Figure title.
    ymin : float, optional, default:0.
        Min score value.
    ymax : float, optional, default:1.
        Max score value.
    tlim : array, (train_min_max, test_min_max) | None, optional, 
        default: None
    ax : object | None, optional, default: None
        Plot pointer. If None, generate new figure.
    show : bool, optional, default: True
        plt.show()
    color : str, optional, default: 'b'
        Score line color.     

    Returns
    -------
    ax : object
        Plot pointer.
    """

    if ax is None:
        fig, ax = plt.subplots(1, 1)
    # detect whether gat is a full matrix or just its diagonal
    if np.all(np.unique([len(t) for t in gat.test_times['s']]) == 1):
        scores = gat.scores
    else:
        scores = np.diag(gat.scores)
    ax.plot(gat.train_times['s'], scores, color=color, label="Classif. score")
    ax.axhline(0.5, color='k', linestyle='--', label="Chance level")
    ax.set_ylim(ymin, ymax)
    ax.set_xlabel('Time (s)')
    ax.set_ylabel(gat.scorer.func_name)
    ax.legend(loc='best')
    if show:
        plt.show()
    return ax


#######################################################################
# Previous time generalization script

@deprecated("'time_generalization' and its auxiliary functions will be removed"
            " in v0.9. Use 'GeneralizationAcrossTime' instead.")
def _one_fold(clf, scorer, X, y, X_gen, y_gen, train, test, train_slices,
              test_slices, n_jobs=1):
    """Aux function of time_generalization

    Parameters
    ----------
    clf : object
        Sklearn classifier
    scorer : object
        Sklearn object
    X : array, shape (n_trials, n_features, n_times)
        To-be-fitted data
    y : list | array, shape (n_trials)
        To-be-fitted model
    X_gen : array, shape (m_trials, n_features, n_times)
        Data used solely for clf testing
    y_gen : list | array, shape (m_trials)
        Model used solely for clf testing

    Returns
    -------
    scores : array
        Classification scores at each training/testing sample.
    scores_gen : array
        Classification scores of generalization set at each training/testing
        sample.
    tested : bool array
        Indicate which training/testing sample was used.
    """

    from sklearn.base import clone

    # Initialize results
    n_train_t = max([t.stop for t in train_slices])  # get maximum time sample
    n_test_t = max([t.stop for tt in test_slices for t in tt])
    scores = np.zeros((n_train_t, n_test_t))  # scores
    tested = np.zeros((n_train_t, n_test_t), dtype=bool)  # tested time points
    if (X_gen is not None) and (y_gen is not None):
        scores_gen = np.zeros((n_train_t, n_test_t))
    else:
        scores_gen = None

    # Loop across time points
    # Parallel across training time
    parallel, p_time_gen, _ = parallel_func(_time_loop, n_jobs)
    packed = parallel(p_time_gen(clone(clf), scorer, X, y, train, test,
                                 X_gen, y_gen, train_slices[t_train],
                                 test_slices[t_train])
                      for t_train in range(len(train_slices)))
    # Unpack results in temporary variables
    scores_, scores_gen_, tested_ = zip(*packed)

    # Store results in absolute sampling-time
    for t_train, train_time in enumerate(train_slices):
        for t_test, test_time in enumerate(test_slices[t_train]):
            scores[train_time.start, test_time.start] = scores_[
                t_train][t_test]
            tested[train_time.start, test_time.start] = tested_[
                t_train][t_test]
            if (X_gen is not None) and (y_gen is not None):
                scores_gen[train_time.start, test_time.start] = scores_gen_[
                    t_train][t_test]
    return scores, scores_gen, tested


def _time_loop(clf, scorer, X, y, train, test, X_gen, y_gen, train_slice,
               test_slices):
    # Initialize results
    scores = []  # scores
    tested = []  # tested time points
    scores_gen = []  # generalization score
    # Flatten features
    my_reshape = lambda X: X.reshape(len(X), np.prod(X.shape[1:]))
    # Select training set
    X_train = my_reshape(X[train, :, train_slice])
    # Fit classifier
    clf.fit(X_train, y[train])
    # Test classification performance across testing time
    for test_slice in test_slices:
        # Select testing time slice
        X_test = my_reshape(X[test, :, test_slice])
        # Evaluate classifer on cross-validation set
        # and store result in relative sampling-time
        scores.append(scorer(clf, X_test, y[test]))
        tested.append(True)
        # Evaluate classifier on cross-condition generalization set
        if (X_gen is not None) and (y_gen is not None):
            x_gen = my_reshape(X_gen[:, :, test_slice])
            scores_gen.append(scorer(clf, x_gen, y_gen))

    return scores, scores_gen, tested


def _compress_results(scores, tested):
    """"
    Avoids returning partially empty results by removing empty lines and
    columns (generally due to slice length > 1).
    """
    scores = scores[:, np.any(tested, axis=0)]
    scores = scores[np.any(tested, axis=1), :]
    return scores


def _gen_type(n_samples, relative_test_slice=False, train_slices=None,
              test_slices=None):
    """ Creates typical temporal generalization scenarios

    The function return train_slices, test_slices that indicate the time
    samples to be used for training and testing each classifier. These
    lists can be directly used by time_generalization_Xy()

    Parameters
    ----------
    n_samples : int
        Number of time samples in each trial | Last sample to on which the
        classifier can be trained
    relative_test_slice : bool
        True implies that the samples indicated in test_slices are relative to
        the samples in train_slices. False implies that the samples in 
        test_slices corresponds to the actual data samples.
    train_slices : list | callable | None
        List of slices generated with create_slices(). By default the
        classifiers are trained on all time points (i.e.
        create_slices(n_time)).
    test_slices : list |  callable | None
        List of slices generated with create_slices(). By default the
        classifiers are tested on all time points (i.e.
        [create_slices(n_time)] * n_time).
    """
    from ..utils import create_slices  # To be deprecated in v0.10

    # Setup train slices
    if train_slices is None:
        # default: train and test over all time samples
        train_slices = create_slices(0, n_samples)
    elif callable(train_slices):
        # create slices once n_slices is known
        train_slices = train_slices(0, n_samples)

    # Setup test slices
    if not relative_test_slice:
        # Time generalization is from/to particular time samples
        if test_slices is None:
            # Default: testing time is identical to training time
            test_slices = [train_slices] * len(train_slices)
        elif callable(test_slices):
            test_slices = [test_slices(n_samples)] * len(train_slices)

    else:
        # Time generalization is at/around the training time samples
        if test_slices is None:
            # Default: testing times are identical to training slices
            # (classic decoding across time)
            test_slices = [[s] for s in train_slices]
        else:
            # Update slice by combining timing of test and train slices
            up_slice = lambda test, train: slice(test.start + train.start,
                                                 test.stop + train.stop - 1,
                                                 train.step)

            test_slices = np.tile(
                [test_slices], (len(train_slices), 1)).tolist()
            for t_train in range(len(train_slices)):
                for t_test in range(len(test_slices[t_train])):
                    # Add start and stop of training and testing slices
                    # to make testing timing dependent on training timing
                    test_slices[t_train][t_test] = up_slice(
                        test_slices[t_train][t_test],
                        train_slices[t_train])

    # Check that all time samples are in bounds
    if any([(s.start < 0) or (s.stop > n_samples) for s in train_slices]) or \
       any([(s.start < 0) or (s.stop > n_samples) for ss in test_slices
            for s in ss]):
        logger.info('/!\ Slicing: time samples out of bound!')

        # Shortcut to select slices that are in bounds
        sel = lambda slices, bol: [s for (s, b) in zip(slices, bol) if b]

        # Deal with testing slices first:
        for t_train in range(len(test_slices)):
            # Find testing slices that are in bounds
            inbound = [(s.start >= 0) and (s.stop <= n_samples)
                       for s in test_slices[t_train]]
            test_slices[t_train] = sel(test_slices[t_train], inbound)

        # Deal with training slices then:
        inbound = [(s.start >= 0) and (s.stop <= n_samples)
                   for s in train_slices]
        train_slices = sel(train_slices, inbound)

    return train_slices, test_slices


@verbose
def time_generalization(epochs_list, epochs_list_gen=None, clf=None,
                        scoring="roc_auc", cv=5, train_slices=None,
                        test_slices=None, relative_test_slice=False,
                        shuffle=True, random_state=None,
                        compress_results=True, n_jobs=1,
                        parallel_across='folds', verbose=None):
    """Fit decoder at each time instant and test at all others

    The function returns the cross-validation scores when the train set
    is from one time instant and the test from all others.

    The decoding will be done using all available data channels, but
    will only work if 1 type of channel is availalble. For example
    epochs should contain only gradiometers.

    Parameters
    ----------
    epochs_list : list
        These epochs are used to train the classifiers (using a cross-
        validation scheme).
    epochs_list_gen : list | None
        Epochs used to test the classifiers' generalization performance
        in novel experimental conditions.
    clf : object | None
        A object following scikit-learn estimator API (fit & predict).
        If None the classifier will be a linear SVM (C=1.) after
        feature standardization.
    cv : int | object
        If an integer is passed, it is the number of fold (default 5).
        Specific cross-validation objects can be passed, see
        sklearn.cross_validation module for the list of possible objects.
    scoring : {string, callable, None}, optional, default: "roc_auc"
        A string (see model evaluation documentation in scikit-learn) or
        a scorer callable object / function with signature
        ``scorer(estimator, X, y)``.
    shuffle : bool
        If True, shuffle the epochs before splitting them in folds.
    random_state : None | int
        The random state used to shuffle the epochs. Ignored if
        shuffle is False.
    train_slices : list | callable | None
        List of slices generated with create_slices(). By default the
        classifiers are trained on all time points (i.e.
        create_slices(n_time)).
    test_slices : list |  callable | None
        List of slices generated with create_slices(). By default the
        classifiers are tested on all time points (i.e.
        [create_slices(n_time)] * n_time).
    relative_test_slice: bool
        True implies that the samples indicated in test_slices are relative to
        the samples in train_slices. False implies that the samples in 
        test_slices corresponds to the actual data samples.
    compress_results : bool
        If true returns only training/tested time samples.
    n_jobs : int
        Number of jobs to run in parallel. Each fold is fit
        in parallel.
    parallel_across : str, 'folds' | 'time_samples'
        Set the parallel (multi-core) computation across folds or across
        time samples.

    Returns
    -------
    out : dict
        'scores' : array, shape (training_slices, testing_slices)
                   The cross-validated scores averaged across folds. 
                   scores[i, j] contains  the generalization score when 
                   learning at time j and testing at time i. The diagonal
                   is the cross-validation score at each time-independant 
                   instant.
        'scores_gen' : array, shape (training_slices, testing_slices)
                       identical to scores for cross-condition generalization
                       (i.e. epochs_list_gen)
        'train_times' : first time samples used to train each classifier
        'train_times' : first time samples used to test each classifier

    Notes
    -----
    The function implements the method used in:

    Jean-Remi King, Alexandre Gramfort, Aaron Schurger, Lionel Naccache
    and Stanislas Dehaene, "Two distinct dynamic modes subtend the detection 
    of unexpected sounds", PLOS ONE, 2013
    """
    from sklearn.base import clone
    from sklearn.utils import check_random_state
    from sklearn.svm import SVC
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.cross_validation import check_cv
    from sklearn.metrics import SCORERS
    from ..pick import channel_type, pick_types

    # Extract MNE data
    info = epochs_list[0].info
    data_picks = pick_types(info, meg=True, eeg=True, exclude='bads')

    # Make arrays X and y such that :
    # X is 3d with X.shape[0] is the total number of epochs to classify
    # y is filled with integers coding for the class to predict
    # We must have X.shape[0] equal to y.shape[0]
    X = [e.get_data()[:, data_picks, :] for e in epochs_list]
    y = [k * np.ones(len(this_X)) for k, this_X in enumerate(X)]
    X = np.concatenate(X)
    y = np.concatenate(y)
    n_trials, n_channels, n_samples = X.shape

    # Apply same procedure with optional generalization set
    if epochs_list_gen is None:
        X_gen, y_gen = None, None
    else:
        info = epochs_list_gen[0].info
        data_picks = pick_types(info, meg=True, eeg=True, exclude='bads')
        X_gen = [e.get_data()[:, data_picks, :]
                 for e in epochs_list_gen]
        y_gen = [k * np.ones(len(this_X)) for k, this_X in enumerate(X_gen)]
        X_gen = np.concatenate(X_gen)
        y_gen = np.concatenate(y_gen)

    # check data sets
    assert(X.shape[0] == y.shape[0] == n_trials)
    if X_gen is not None and y_gen is not None:
        assert(X_gen.shape[0] == y_gen.shape[0])

    # re-order data to avoid taking to avoid folding bias
    if shuffle:
        rng = check_random_state(random_state)
        order = np.argsort(rng.randn(n_trials))
        X = X[order]
        y = y[order]

    # Set default MVPA: support vector classifier
    if clf is None:
        scaler = StandardScaler()
        svc = SVC(C=1, kernel='linear')
        clf = Pipeline([('scaler', scaler), ('svc', svc)])

    # Set default cross validation scheme
    cv = check_cv(cv, X, y, classifier=True)

    # Set default scoring scheme
    if type(scoring) is str:
        scorer = SCORERS[scoring]
    else:
        scorer = scoring

    # Set default train and test slices
    train_slices, test_slices = _gen_type(n_samples,
                                          relative_test_slice=relative_test_slice,
                                          train_slices=train_slices,
                                          test_slices=test_slices)

    # Chose parallization type
    if parallel_across == 'folds':
        n_jobs_time = 1
        n_jobs_fold = n_jobs
    elif parallel_across == 'time_samples':
        n_jobs_time = n_jobs
        n_jobs_fold = 1

    # Launch main script
    ch_types = set([channel_type(info, idx) for idx in data_picks])
    logger.info('Running time generalization on %s epochs using %s.' %
                (len(X), ch_types.pop()))

    # Cross-validation loop
    parallel, p_time_gen, _ = parallel_func(_one_fold, n_jobs_fold)
    packed = parallel(p_time_gen(clone(clf), scorer, X, y, X_gen,
                                 y_gen, train, test, train_slices, test_slices,
                                 n_jobs=n_jobs_time)
                      for train, test in cv)

    # Unpack MVPA results from parallel outputs
    scores, scores_gen, tested = zip(*packed)

    # Mean scores across folds
    scores = np.mean(scores, axis=0)
    tested = tested[0]

    # Simplify results
    if compress_results:
        scores = _compress_results(scores, tested)

    # Output results in a dictionary to allow future extensions
    out = dict(scores=scores)
    if X_gen is not None:
        scores_gen = np.mean(scores_gen, axis=0)
        if compress_results:
            scores_gen = _compress_results(scores_gen, tested)
        out['scores_gen'] = scores_gen

    out['train_times'] = epochs_list[0].times[
        [s.start for s in train_slices]]
    out['test_times'] = epochs_list[0].times[
        [s.start for s in test_slices[0]]]

    return out
