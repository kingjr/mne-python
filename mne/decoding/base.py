"""Base class copy from sklearn.base."""
# Authors: Gael Varoquaux <gael.varoquaux@normalesup.org>
#          Romain Trachel <trachelr@gmail.com>
#          Alexandre Gramfort <alexandre.gramfort@telecom-paristech.fr>
#          Jean-Remi King <jeanremi.king@gmail.com>
#
# License: BSD (3-clause)

import numpy as np
import time
import numbers
from ..parallel import parallel_func
from ..fixes import BaseEstimator
from ..utils import check_version, requires_sklearn_0_15, logger, warn


class LinearModel(BaseEstimator):
    """Compute and store patterns from linear models.

    The linear model coefficients (filters) are used to extract discriminant
    neural sources from the measured data. This class computes the
    corresponding patterns of these linear filters to make them more
    interpretable [1]_.

    Parameters
    ----------
    model : object | None
        A linear model from scikit-learn with a fit method
        that updates a ``coef_`` attribute.
        If None the model will be LogisticRegression.

    Attributes
    ----------
    ``filters_`` : ndarray
        If fit, the filters used to decompose the data.
    ``patterns_`` : ndarray
        If fit, the patterns used to restore M/EEG signals.

    Notes
    -----
    .. versionadded:: 0.10

    See Also
    --------
    CSP
    mne.preprocessing.ICA
    mne.preprocessing.Xdawn

    References
    ----------
    .. [1] Haufe, S., Meinecke, F., Gorgen, K., Dahne, S., Haynes, J.-D.,
           Blankertz, B., & Biebmann, F. (2014). On the interpretation of
           weight vectors of linear models in multivariate neuroimaging.
           NeuroImage, 87, 96-110.
    """

    def __init__(self, model=None):  # noqa: D102
        if model is None:
            from sklearn.linear_model import LogisticRegression
            model = LogisticRegression()

        self.model = model

    def fit(self, X, y):
        """Estimate the coefficients of the linear model.

        Save the coefficients in the attribute ``filters_`` and
        computes the attribute ``patterns_``.

        Parameters
        ----------
        X : array, shape (n_samples, n_features)
            The training input samples to estimate the linear coefficients.
        y : array, shape (n_samples,)
            The target values.

        Returns
        -------
        self : instance of LinearModel
            Returns the modified instance.
        """
        X = np.asarray(X)
        if X.ndim != 2:
            raise ValueError('LinearModel only accepts 2-dimensional X, got '
                             '%s instead.' % (X.shape,))

        # fit the Model
        self.model.fit(X, y)

        # computes the patterns
        if (
            not hasattr(self.model, 'coef_') or  # missing attribute
            self.model.coef_.ndim > 2 or         # weird case
            (self.model.coef_.size not in
             self.model.coef_.shape)             # shape (n), (n, 1) or (1, n)
        ):

            raise ValueError('model needs a unidimensional coef_ attribute to '
                             'compute the patterns')
        self.filters_ = np.squeeze(self.model.coef_)
        self.patterns_ = np.cov(X.T).dot(self.filters_)

        return self

    def transform(self, X):
        """Transform the data using the linear model.

        Parameters
        ----------
        X : array, shape (n_samples, n_features)
            The data to transform.

        Returns
        -------
        y_pred : array, shape (n_samples,)
            The predicted targets.
        """
        return self.model.transform(X)

    def fit_transform(self, X, y):
        """Fit the data and transform it using the linear model.

        Parameters
        ----------
        X : array, shape (n_samples, n_features)
            The training input samples to estimate the linear coefficients.
        y : array, shape (n_samples,)
            The target values.

        Returns
        -------
        y_pred : array, shape (n_samples,)
            The predicted targets.

        """
        return self.fit(X, y).transform(X)

    def predict(self, X):
        """Compute predictions of y from X.

        Parameters
        ----------
        X : array, shape (n_samples, n_features)
            The data used to compute the predictions.

        Returns
        -------
        y_pred : array, shape (n_samples,)
            The predictions.
        """
        return self.model.predict(X)

    def score(self, X, y):
        """Score the linear model computed on the given test data.

        Parameters
        ----------
        X : array, shape (n_samples, n_features)
            The data to transform.
        y : array, shape (n_samples,)
            The target values.

        Returns
        -------
        score : float
            Score of the linear model
        """
        return self.model.score(X, y)


def _set_cv(cv, estimator=None, X=None, y=None):
    """Set the default CV depending on whether clf is classifier/regressor."""
    from sklearn.base import is_classifier

    # Detect whether classification or regression
    if estimator in ['classifier', 'regressor']:
        est_is_classifier = estimator == 'classifier'
    else:
        est_is_classifier = is_classifier(estimator)
    # Setup CV
    if check_version('sklearn', '0.18'):
        from sklearn import model_selection as models
        from sklearn.model_selection import (check_cv, StratifiedKFold, KFold)
        if isinstance(cv, (int, np.int)):
            XFold = StratifiedKFold if est_is_classifier else KFold
            cv = XFold(n_splits=cv)
        elif isinstance(cv, str):
            if not hasattr(models, cv):
                raise ValueError('Unknown cross-validation')
            cv = getattr(models, cv)
            cv = cv()
        cv = check_cv(cv=cv, y=y, classifier=est_is_classifier)
    else:
        from sklearn import cross_validation as models
        from sklearn.cross_validation import (check_cv, StratifiedKFold, KFold)
        if isinstance(cv, (int, np.int)):
            if est_is_classifier:
                cv = StratifiedKFold(y=y, n_folds=cv)
            else:
                cv = KFold(n=len(y), n_folds=cv)
        elif isinstance(cv, str):
            if not hasattr(models, cv):
                raise ValueError('Unknown cross-validation')
            cv = getattr(models, cv)
            if cv.__name__ not in ['KFold', 'LeaveOneOut']:
                raise NotImplementedError('CV cannot be defined with str for'
                                          ' sklearn < .017.')
            cv = cv(len(y))
        cv = check_cv(cv=cv, X=X, y=y, classifier=est_is_classifier)

    # Extract train and test set to retrieve them at predict time
    if hasattr(cv, 'split'):
        cv_splits = [(train, test) for train, test in
                     cv.split(X=np.zeros_like(y), y=y)]
    else:
        # XXX support sklearn.cross_validation cv
        cv_splits = [(train, test) for train, test in cv]

    if not np.all([len(train) for train, _ in cv_splits]):
        raise ValueError('Some folds do not have any train epochs.')

    return cv, cv_splits


def _check_estimator(estimator, get_params=True):
    """Check whether an object has the methods required by sklearn."""
    valid_methods = ('predict', 'transform', 'predict_proba',
                     'decision_function')
    if (
        (not hasattr(estimator, 'fit')) or
        (not any(hasattr(estimator, method) for method in valid_methods))
    ):
        raise ValueError('estimator must be a scikit-learn transformer or '
                         'an estimator with the fit and a predict-like (e.g. '
                         'predict_proba) or a transform method.')

    if get_params and not hasattr(estimator, 'get_params'):
        raise ValueError('estimator must be a scikit-learn transformer or an '
                         'estimator with the get_params method that allows '
                         'cloning.')


def _get_inverse_funcs(estimator, terminal=True):
    """Retrieve the inverse functions of an pipeline or an estimator."""
    inverse_func = [False]
    if hasattr(estimator, 'steps'):
        # if pipeline, retrieve all steps by nesting
        inverse_func = list()
        for _, est in estimator.steps:
            inverse_func.extend(_get_inverse_funcs(est, terminal=False))
    elif hasattr(estimator, 'inverse_transform'):
        # if not pipeline attempt to retrieve inverse function
        inverse_func = [estimator.inverse_transform]

    # If terminal node, check that that the last estimator is a classifier,
    # and remove it from the transformers.
    if terminal:
        last_is_estimator = inverse_func[-1] is False
        all_invertible = not(False in inverse_func[:-1])
        if last_is_estimator and all_invertible:
            # keep all inverse transformation and remove last estimation
            inverse_func = inverse_func[:-1]
        else:
            inverse_func = list()

    return inverse_func


def get_coef(estimator, attr='filters_', inverse_transform=False):
    """Retrieve the coefficients of an estimator ending with a Linear Model.

    This is typically useful to retrieve "spatial filters" or "spatial
    patterns" of decoding models [1]_.

    Parameters
    ----------
    estimator : object | None
        An estimator from scikit-learn.
    attr : str
        The name of the coefficient attribute to retrieve, typically
        ``'filters_'`` (default) or ``'patterns_'``.
    inverse_transform : bool
        If True, returns the coefficients after inverse transforming them with
        the transformer steps of the estimator.

    Returns
    -------
    coef : array
        The coefficients.

    References
    ----------
    .. [1] Haufe, S., Meinecke, F., Gorgen, K., Dahne, S., Haynes, J.-D.,
       Blankertz, B., & Biessmann, F. (2014). On the interpretation of weight
       vectors of linear models in multivariate neuroimaging. NeuroImage, 87,
       96-110. doi:10.1016/j.neuroimage.2013.10.067.
    """
    # If searchlight, loop across estimators
    if hasattr(estimator, 'estimators_'):
        coef = list()
        for est in estimator.estimators_:
            coef.append(get_coef(est, attr, inverse_transform))
        return np.array(coef)

    else:
        # Get the coefficients of the last estimator in case of nested pipeline
        est = estimator
        while hasattr(est, 'steps'):
            est = est.steps[-1][1]
        if not hasattr(est, attr):
            raise ValueError('This estimator does not have a %s '
                             'attribute.' % attr)
        coef = getattr(est, attr)

        # inverse pattern e.g. to get back physical units
        if inverse_transform:
            if not hasattr(estimator, 'steps'):
                raise ValueError('inverse_transform can only be applied onto '
                                 'pipeline estimators.')

            # The inverse_transform parameter will call this method on any
            # estimator contained in the pipeline, in reverse order.
            for inverse_func in _get_inverse_funcs(estimator)[::-1]:
                coef = inverse_func([coef])[0]
        return coef


@requires_sklearn_0_15
def cross_val_multiscore(estimator, X, y=None, groups=None, scoring=None,
                         cv=None, n_jobs=1, verbose=0, fit_params=None,
                         pre_dispatch='2*n_jobs'):
    """Evaluate a score by cross-validation

    Parameters
    ----------
    estimator : estimator object implementing 'fit'
        The object to use to fit the data.

    X : array-like
        The data to fit. Can be, for example a list, or an array at least 2d.

    y : array-like, optional, default: None
        The target variable to try to predict in the case of
        supervised learning.

    groups : array-like, with shape (n_samples,), optional
        Group labels for the samples used while splitting the dataset into
        train/test set.

    scoring : string, callable or None, optional, default: None
        A string (see model evaluation documentation) or
        a scorer callable object / function with signature
        ``scorer(estimator, X, y)``.

    cv : int, cross-validation generator or an iterable, optional
        Determines the cross-validation splitting strategy.
        Possible inputs for cv are:
          - None, to use the default 3-fold cross validation,
          - integer, to specify the number of folds in a `(Stratified)KFold`,
          - An object to be used as a cross-validation generator.
          - An iterable yielding train, test splits.

        For integer/None inputs, if the estimator is a classifier and ``y`` is
        either binary or multiclass, :class:`StratifiedKFold` is used. In all
        other cases, :class:`KFold` is used.

        Refer :ref:`User Guide <cross_validation>` for the various
        cross-validation strategies that can be used here.

    n_jobs : integer, optional
        The number of CPUs to use to do the computation. -1 means
        'all CPUs'.

    verbose : integer, optional
        The verbosity level.

    fit_params : dict, optional
        Parameters to pass to the fit method of the estimator.

    pre_dispatch : int, or string, optional
        Controls the number of jobs that get dispatched during parallel
        execution. Reducing this number can be useful to avoid an
        explosion of memory consumption when more jobs get dispatched
        than CPUs can process. This parameter can be:

            - None, in which case all the jobs are immediately
              created and spawned. Use this for lightweight and
              fast-running jobs, to avoid delays due to on-demand
              spawning of the jobs

            - An int, giving the exact number of total jobs that are
              spawned

            - A string, giving an expression as a function of n_jobs,
              as in '2*n_jobs'

    Returns
    -------
    scores : array of float, shape=(len(list(cv)),) | array of array
        Array of scores of the estimator for each run of the cross validation.

    """
    # This code is copied from sklearn

    from sklearn.base import is_classifier, clone
    from sklearn.utils import indexable
    from sklearn.metrics.scorer import check_scoring
    from sklearn.model_selection._split import check_cv

    X, y, groups = indexable(X, y, groups)

    cv = check_cv(cv, y, classifier=is_classifier(estimator))
    cv_iter = list(cv.split(X, y, groups))
    scorer = check_scoring(estimator, scoring=scoring)
    # We clone the estimator to make sure that all the folds are
    # independent, and that it is pickle-able.
    # Note: this parallelization is implemented using MNE Parallel
    parallel, p_func, n_jobs = parallel_func(_fit_and_score, n_jobs,
                                             pre_dispatch=pre_dispatch)
    scores = parallel(p_func(clone(estimator), X, y, scorer, train, test,
                             verbose, None, fit_params)
                      for train, test in cv_iter)
    return np.array(scores)[:, 0, ...]  # flatten over joblib output.


def _fit_and_score(estimator, X, y, scorer, train, test, verbose,
                   parameters, fit_params, return_train_score=False,
                   return_parameters=False, return_n_test_samples=False,
                   return_times=False, error_score='raise'):
    """Fit estimator and compute scores for a given dataset split.

    Parameters
    ----------
    estimator : estimator object implementing 'fit'
        The object to use to fit the data.

    X : array-like of shape at least 2D
        The data to fit.

    y : array-like, optional, default: None
        The target variable to try to predict in the case of
        supervised learning.

    scorer : callable
        A scorer callable object / function with signature
        ``scorer(estimator, X, y)``.

    train : array-like, shape (n_train_samples,)
        Indices of training samples.

    test : array-like, shape (n_test_samples,)
        Indices of test samples.

    verbose : integer
        The verbosity level.

    error_score : 'raise' (default) or numeric
        Value to assign to the score if an error occurs in estimator fitting.
        If set to 'raise', the error is raised. If a numeric value is given,
        FitFailedWarning is raised. This parameter does not affect the refit
        step, which will always raise the error.

    parameters : dict or None
        Parameters to be set on the estimator.

    fit_params : dict or None
        Parameters that will be passed to ``estimator.fit``.

    return_train_score : boolean, optional, default: False
        Compute and return score on training set.

    return_parameters : boolean, optional, default: False
        Return parameters that has been used for the estimator.

    Returns
    -------
    train_score : float, optional
        Score on training set, returned only if `return_train_score` is `True`.

    test_score : float
        Score on test set.

    n_test_samples : int
        Number of test samples.

    fit_time : float
        Time spent for fitting in seconds.

    score_time : float
        Time spent for scoring in seconds.

    parameters : dict or None, optional
        The parameters that have been evaluated.
    """
    #  This code is adapted from sklearn
    from sklearn.model_selection._validation import _index_param_value
    from sklearn.utils.metaestimators import _safe_split
    from sklearn.utils.validation import _num_samples

    if verbose > 1:
        if parameters is None:
            msg = ''
        else:
            msg = '%s' % (', '.join('%s=%s' % (k, v)
                          for k, v in parameters.items()))
        print("[CV] %s %s" % (msg, (64 - len(msg)) * '.'))

    # Adjust length of sample weights
    fit_params = fit_params if fit_params is not None else {}
    fit_params = dict([(k, _index_param_value(X, v, train))
                      for k, v in fit_params.items()])

    if parameters is not None:
        estimator.set_params(**parameters)

    start_time = time.time()

    X_train, y_train = _safe_split(estimator, X, y, train)
    X_test, y_test = _safe_split(estimator, X, y, test, train)

    try:
        if y_train is None:
            estimator.fit(X_train, **fit_params)
        else:
            estimator.fit(X_train, y_train, **fit_params)

    except Exception as e:
        # Note fit time as time until error
        fit_time = time.time() - start_time
        score_time = 0.0
        if error_score == 'raise':
            raise
        elif isinstance(error_score, numbers.Number):
            test_score = error_score
            if return_train_score:
                train_score = error_score
            warn("Classifier fit failed. The score on this train-test"
                 " partition for these parameters will be set to %f. "
                 "Details: \n%r" % (error_score, e))
        else:
            raise ValueError("error_score must be the string 'raise' or a"
                             " numeric value. (Hint: if using 'raise', please"
                             " make sure that it has been spelled correctly.)")

    else:
        fit_time = time.time() - start_time
        test_score = _score(estimator, X_test, y_test, scorer)
        score_time = time.time() - start_time - fit_time
        if return_train_score:
            train_score = _score(estimator, X_train, y_train, scorer)

    if verbose > 2:
        msg += ", score=%f" % test_score
    if verbose > 1:
        total_time = score_time + fit_time
        end_msg = "%s, total=%s" % (msg, logger.short_format_time(total_time))
        print("[CV] %s %s" % ((64 - len(end_msg)) * '.', end_msg))

    ret = [train_score, test_score] if return_train_score else [test_score]

    if return_n_test_samples:
        ret.append(_num_samples(X_test))
    if return_times:
        ret.extend([fit_time, score_time])
    if return_parameters:
        ret.append(parameters)
    return ret


def _score(estimator, X_test, y_test, scorer):
    """Compute the score of an estimator on a given test set.

    This code is the same as sklearn.model_selection._validation._score
    but accepts to output arrays instead of floats.
    """

    if y_test is None:
        score = scorer(estimator, X_test)
    else:
        score = scorer(estimator, X_test, y_test)
    if hasattr(score, 'item'):
        try:
            # e.g. unwrap memmapped scalars
            score = score.item()
        except ValueError:
            # non-scalar?
            pass
    return score
