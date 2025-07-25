import inspect
import math
from typing import Protocol, runtime_checkable, Self
from warnings import warn, catch_warnings

import numpy as np
from numpy.linalg import inv
from scipy import optimize, spatial

from .._shared.utils import (
    _deprecate_estimate,
    FailedEstimation,
    deprecate_parameter,
    deprecate_func,
    DEPRECATED,
)

_EPSILON = np.spacing(1)


def _check_data_dim(data, dim):
    if data.ndim != 2 or data.shape[1] != dim:
        raise ValueError(f"Input data must have shape (N, {dim}).")


def _check_data_atleast_2D(data):
    if data.ndim < 2 or data.shape[1] < 2:
        raise ValueError('Input data must be at least 2D.')


@runtime_checkable
class RansacModelProtocol(Protocol):
    """Protocol for `ransac` model class."""

    @classmethod
    def from_estimate(cls, *data): ...

    def residuals(self, *data): ...


_PARAMS_DEP_START = '0.26'
_PARAMS_DEP_STOP = '2.2'


class BaseModel:
    def __init_subclass__(self):
        warn(
            f'`BaseModel` deprecated since version {_PARAMS_DEP_START} and '
            f'will be removed in version {_PARAMS_DEP_STOP}',
            category=FutureWarning,
            stacklevel=2,
        )


class _BaseModel:
    """Implement common methods for model classes.

    This class can be removed when we expire deprecations of ``estimate``
    method, and `params` arguments to ``predict*`` methods.

    Note that each inheriting class will need to implement
    ``_params2init_values``, that breaks up the ``params`` vector into separate
    components comprising the arguments to the function ``__init__``, and
    checks the resulting input arguments for validity.
    """

    @classmethod
    def from_estimate(cls, data) -> Self | FailedEstimation:
        # In order to defer to the ``_estimate`` method, we first need to
        # create an empty not-initialized instance, that we can override by
        # executing the ``_estimate`` method.  This relies on the assumption
        # that `_estimate` can work with an uninitialized instance.  This
        # assumption only need hold until we can expire the deprecation of the
        # `estimate` method, at which point we can move the estimation logic
        # from the ``_estimate`` methods, to the respective ``from_estimate``
        # class methods.
        with catch_warnings(action='ignore'):
            tf = cls()
        msg = tf._estimate(data, warn_only=False)
        return tf if msg is None else FailedEstimation(f'{cls.__name__}: {msg}')

    def _get_init_values(self, params):
        if params is None or params is DEPRECATED:
            if getattr(self, self._init_args[0]) is None:
                # Until the deprecation of no-argument initialization expires,
                # it is easy to create a not-initialized model, evidenced by
                # None values of the init attributes.
                cls_name = type(self).__name__
                raise ValueError(
                    '`params` argument must be specified when '
                    'applied to model initialized with '
                    f'``{cls_name}()``; Consider creating new '
                    f'{cls_name} with suitable input arguments, '
                    f'or by using ``{cls_name}.from_estimate``.'
                )
            return [getattr(self, a) for a in self._init_args]
        return self._params2init_values(params)


def _warn_or_msg(msg, warn_only=True):
    """If `warn_only`, warn with `msg`, return ``None``, else return `msg`

    For `from_estimate` API, we want to return a ``FailedEstimation`` for these
    estimation failures, which we do by setting ``warn_only=False``, and
    passing back the `msg` from the ``_estimation`` method via this function.
    For the deprecated ``estimate`` API, we want to warn (``warn_only=True``),
    and return an incomplete transform.  The ``None`` return value indicates
    the estimation has kind-of succeeded, for back compatibility.
    """
    if not warn_only:
        return msg
    warn(msg, category=RuntimeWarning, stacklevel=5)
    return None


def _deprecate_no_args(cls):
    """Class decorator to allow, deprecate no input arguments to ``__init__``.

    Makes a new ``__init__`` method, that a) will allow option of passing no
    arguments, and b) when used thus, raises a deprecation warning.  Otherwise
    defers to an assumed-existing ``_args_init`` instance method to deal with
    input arguments.  If there are no parameters, set desired parameters to
    None, to signal uninitialized object.

    At the end of deprecation we can drop this decorator, and rename
    ``_args_init`` to ``__init__``.
    """

    args_init_sig = inspect.signature(cls._args_init)
    cls._init_args = [k for k in args_init_sig.parameters if k != 'self']

    def init(self, *args, **kwargs):
        if len(args) or len(kwargs):
            self._args_init(*args, **kwargs)
            return
        warn(
            f'Calling ``{cls.__name__}()`` (without arguments) has been '
            f'deprecated since version {_PARAMS_DEP_START} and will be '
            f'removed in version {_PARAMS_DEP_STOP}; see help for '
            f'``{cls.__name__}``.',
            category=FutureWarning,
            stacklevel=2,
        )
        # Blank initialization.
        for k in cls._init_args:
            setattr(self, k, None)

    init.__signature__ = args_init_sig
    cls.__init__ = init
    return cls


def _deprecate_model_params(func):
    """Deprecate `params` argument of various model methods."""
    func = deprecate_parameter(
        'params',
        start_version=_PARAMS_DEP_START,
        stop_version=_PARAMS_DEP_STOP,
        modify_docstring=False,
    )(func)
    func.__doc__ = func.__doc__.replace('{{ start_version }}', _PARAMS_DEP_START)
    return func


@_deprecate_no_args
class LineModelND(_BaseModel):
    """Total least squares estimator for N-dimensional lines.

    In contrast to ordinary least squares line estimation, this estimator
    minimizes the orthogonal distances of points to the estimated line.

    Lines are defined by a point (origin) and a unit vector (direction)
    according to the following vector equation::

        X = origin + lambda * direction

    Parameters
    ----------
    origin : array-like, shape (N,)
        Coordinates of line origin in N dimensions.
    direction : array-like, shape (N,)
        Vector giving line direction.

    Raises
    ------
    ValueError
        If length of `origin` and `direction` differ.

    Examples
    --------
    >>> x = np.linspace(1, 2, 25)
    >>> y = 1.5 * x + 3
    >>> lm = LineModelND.from_estimate(np.stack([x, y], axis=-1))
    >>> lm.origin
    array([1.5 , 5.25])
    >>> lm.direction  # doctest: +FLOAT_CMP
    array([0.5547 , 0.83205])
    >>> res = lm.residuals(np.stack([x, y], axis=-1))
    >>> np.abs(np.round(res, 9))
    array([0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.,
           0., 0., 0., 0., 0., 0., 0., 0.])
    >>> np.round(lm.predict_y(x[:5]), 3)
    array([4.5  , 4.562, 4.625, 4.688, 4.75 ])
    >>> np.round(lm.predict_x(y[:5]), 3)
    array([1.   , 1.042, 1.083, 1.125, 1.167])

    """

    def _args_init(self, origin, direction):
        """Initialize ``LineModelND`` instance.

        Parameters
        ----------
        origin : array-like, shape (N,)
            Coordinates of line origin in N dimensions.
        direction : array-like, shape (N,)
            Vector giving line direction.
        """
        self.origin, self.direction = self._check_init_values(origin, direction)

    def _check_init_values(self, origin, direction):
        origin, direction = (np.array(v) for v in (origin, direction))
        if len(origin) != len(direction):
            raise ValueError('Direction vector should be same length as origin point.')
        return origin, direction

    def _params2init_values(self, params):
        if len(params) != 2:
            raise ValueError('Input `params` should be length 2')
        return self._check_init_values(*params)

    @property
    @deprecate_func(
        deprecated_version=_PARAMS_DEP_START,
        removed_version=_PARAMS_DEP_STOP,
        hint='`params` attribute deprecated; use ``origin, direction`` attributes instead',
    )
    def params(self):
        """Return model attributes as ``origin, direction`` tuple."""
        return self.origin, self.direction

    @classmethod
    def from_estimate(cls, data):
        """Estimate line model from data.

        This minimizes the sum of shortest (orthogonal) distances
        from the given data points to the estimated line.

        Parameters
        ----------
        data : (N, dim) array
            N points in a space of dimensionality dim >= 2.

        Returns
        -------
        model : Self or `~.FailedEstimation`
            An instance of the line model if the estimation succeeded.
            Otherwise, we return a special ``FailedEstimation`` object to
            signal a failed estimation. Testing the truth value of the failed
            estimation object will return ``False``. E.g.

            .. code-block:: python

                model = LineModelND.from_estimate(...)
                if not model:
                    raise RuntimeError(f"Failed estimation: {model}")
        """
        return super().from_estimate(data)

    def _estimate(self, data, warn_only=True):
        _check_data_atleast_2D(data)

        origin = data.mean(axis=0)
        data = data - origin

        if data.shape[0] == 2:  # well determined
            direction = data[1] - data[0]
            norm = np.linalg.norm(direction)
            if norm != 0:  # this should not happen to be norm 0
                direction /= norm
        elif data.shape[0] > 2:  # over-determined
            # Note: with full_matrices=1 Python dies with joblib parallel_for.
            _, _, v = np.linalg.svd(data, full_matrices=False)
            direction = v[0]
        else:  # under-determined
            return 'estimate under-determined'

        self.origin = origin
        self.direction = direction
        return None

    @_deprecate_model_params
    def residuals(self, data, params=DEPRECATED):
        """Determine residuals of data to model.

        For each point, the shortest (orthogonal) distance to the line is
        returned. It is obtained by projecting the data onto the line.

        Parameters
        ----------
        data : (N, dim) array
            N points in a space of dimension dim.

        Returns
        -------
        residuals : (N,) array
            Residual for each data point.

        Other parameters
        ----------------
        params : `~.DEPRECATED`, optional
            Optional custom parameter set in the form (`origin`, `direction`).

            .. deprecated:: {{ start_version }}
        """
        _check_data_atleast_2D(data)
        origin, direction = self._get_init_values(params)
        if len(origin) != data.shape[1]:
            raise ValueError(
                f'`origin` is {len(origin)}D, but `data` is {data.shape[1]}D'
            )
        res = (data - origin) - ((data - origin) @ direction)[
            ..., np.newaxis
        ] * direction
        return np.linalg.norm(res, axis=1)

    @_deprecate_model_params
    def predict(self, x, axis=0, params=DEPRECATED):
        """Predict intersection of line model with orthogonal hyperplane.

        Parameters
        ----------
        x : (n, 1) array
            Coordinates along an axis.
        axis : int
            Axis orthogonal to the hyperplane intersecting the line.

        Returns
        -------
        data : (n, m) array
            Predicted coordinates.

        Other parameters
        ----------------
        params : `~.DEPRECATED`, optional
            Optional custom parameter set in the form (`origin`, `direction`).

            .. deprecated:: {{ start_version }}

        Raises
        ------
        ValueError
            If the line is parallel to the given axis.
        """
        origin, direction = self._get_init_values(params)
        if direction[axis] == 0:
            # line parallel to axis
            raise ValueError(f'Line parallel to axis {axis}')

        l = (x - origin[axis]) / direction[axis]
        data = origin + l[..., np.newaxis] * direction
        return data

    @_deprecate_model_params
    def predict_x(self, y, params=DEPRECATED):
        """Predict x-coordinates for 2D lines using the estimated model.

        Alias for::

            predict(y, axis=1)[:, 0]

        Parameters
        ----------
        y : array
            y-coordinates.

        Returns
        -------
        x : array
            Predicted x-coordinates.

        Other parameters
        ----------------
        params : `~.DEPRECATED`, optional
            Optional custom parameter set in the form (`origin`, `direction`).

            .. deprecated:: {{ start_version }}

        """
        # Avoid triggering deprecationwarning in predict.
        tf = (
            self
            if (params is None or params is DEPRECATED)
            else type(self)(*self._params2init_values(params))
        )
        x = tf.predict(y, axis=1)[:, 0]
        return x

    @_deprecate_model_params
    def predict_y(self, x, params=DEPRECATED):
        """Predict y-coordinates for 2D lines using the estimated model.

        Alias for::

            predict(x, axis=0)[:, 1]

        Parameters
        ----------
        x : array
            x-coordinates.

        Returns
        -------
        y : array
            Predicted y-coordinates.

        Other parameters
        ----------------
        params : `~.DEPRECATED`, optional
            Optional custom parameter set in the form (`origin`, `direction`).

            .. deprecated:: {{ start_version }}

        """
        # Avoid triggering deprecationwarning in predict.
        tf = (
            self
            if (params is None or params is DEPRECATED)
            else type(self)(*self._params2init_values(params))
        )
        y = tf.predict(x, axis=0)[:, 1]
        return y

    @_deprecate_estimate
    def estimate(self, data):
        """Estimate line model from data.

        This minimizes the sum of shortest (orthogonal) distances
        from the given data points to the estimated line.

        Parameters
        ----------
        data : (N, dim) array
            N points in a space of dimensionality ``dim >= 2``.

        Returns
        -------
        success : bool
            True, if model estimation succeeds.
        """
        return self._estimate(data) is None


@_deprecate_no_args
class CircleModel(_BaseModel):
    """Total least squares estimator for 2D circles.

    The functional model of the circle is::

        r**2 = (x - xc)**2 + (y - yc)**2

    This estimator minimizes the squared distances from all points to the
    circle::

        min{ sum((r - sqrt((x_i - xc)**2 + (y_i - yc)**2))**2) }

    A minimum number of 3 points is required to solve for the parameters.

    Parameters
    ----------
    center : array-like, shape (2,)
        Coordinates of circle center.
    radius : float
        Circle radius.

    Notes
    -----
    The estimation is carried out using a 2D version of the spherical
    estimation given in [1]_.

    References
    ----------
    .. [1] Jekel, Charles F. Obtaining non-linear orthotropic material models
           for pvc-coated polyester via inverse bubble inflation.
           Thesis (MEng), Stellenbosch University, 2016. Appendix A, pp. 83-87.
           https://hdl.handle.net/10019.1/98627

    Raises
    ------
    ValueError
        If `center` does not have length 2.

    Examples
    --------
    >>> t = np.linspace(0, 2 * np.pi, 25)
    >>> xy = CircleModel((2, 3), 4).predict_xy(t)
    >>> model = CircleModel.from_estimate(xy)
    >>> model.center
    array([2., 3.])
    >>> model.radius
    4.0
    >>> res = model.residuals(xy)
    >>> np.abs(np.round(res, 9))
    array([0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.,
           0., 0., 0., 0., 0., 0., 0., 0.])

    The estimation can fail when — for example — all the input or output
    points are the same.  If this happens, you will get a transform that is not
    "truthy" - meaning that ``bool(tform)`` is ``False``:

    >>> # A successfully estimated model is truthy:
    >>> if model:
    ...     print("Estimation succeeded.")
    Estimation succeeded.
    >>> # Not so for a degenerate model with identical points.
    >>> bad_data = np.ones((4, 2))
    >>> bad_model = CircleModel.from_estimate(bad_data)
    >>> if not bad_model:
    ...     print("Estimation failed.")
    Estimation failed.

    Trying to use this failed estimation transform result will give a suitable
    error:

    >>> bad_model.residuals(xy)  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
      ...
    FailedEstimationAccessError: No attribute "residuals" for failed estimation ...
    """

    def _args_init(self, center, radius):
        """Initialize CircleModel instance.

        Parameters
        ----------
        center : array-like, shape (2,)
            Coordinates of circle center.
        radius : float
            Circle radius.
        """
        self.center, self.radius = self._check_init_values(center, radius)

    def _check_init_values(self, center, radius):
        center = np.array(center)
        if not len(center) == 2:
            raise ValueError('Center coordinates should be length 2')
        return center, radius

    def _params2init_values(self, params):
        params = np.array(params)
        if len(params) != 3:
            raise ValueError('Input `params` should be length 3')
        return self._check_init_values(params[:2], params[2])

    @property
    @deprecate_func(
        deprecated_version=_PARAMS_DEP_START,
        removed_version=_PARAMS_DEP_STOP,
        hint='`params` attribute deprecated; use `center, radius` attributes instead',
    )
    def params(self):
        """Return model attributes ``center, radius`` as 1D array."""
        return np.r_[self.center, self.radius]

    @classmethod
    def from_estimate(cls, data):
        """Estimate circle model from data using total least squares.

        Parameters
        ----------
        data : (N, 2) array
            N points with ``(x, y)`` coordinates, respectively.

        Returns
        -------
        model : Self or `~.FailedEstimation`
            An instance of the circle model if the estimation succeeded.
            Otherwise, we return a special ``FailedEstimation`` object to
            signal a failed estimation. Testing the truth value of the failed
            estimation object will return ``False``. E.g.

            .. code-block:: python

                model = CircleModel.from_estimate(...)
                if not model:
                    raise RuntimeError(f"Failed estimation: {model}")
        """
        return super().from_estimate(data)

    def _estimate(self, data, warn_only=True):
        _check_data_dim(data, dim=2)

        # to prevent integer overflow, cast data to float, if it isn't already
        float_type = np.promote_types(data.dtype, np.float32)
        data = data.astype(float_type, copy=False)
        # normalize value range to avoid misfitting due to numeric errors if
        # the relative distanceses are small compared to absolute distances
        origin = data.mean(axis=0)
        data = data - origin
        scale = data.std()
        if scale < np.finfo(float_type).tiny:
            return _warn_or_msg(
                "Standard deviation of data is too small to estimate "
                "circle with meaningful precision.",
                warn_only=warn_only,
            )

        data /= scale

        # Adapted from a spherical estimator covered in a blog post by Charles
        # Jeckel (see also reference 1 above):
        # https://jekel.me/2015/Least-Squares-Sphere-Fit/
        A = np.append(data * 2, np.ones((data.shape[0], 1), dtype=float_type), axis=1)
        f = np.sum(data**2, axis=1)
        C, _, rank, _ = np.linalg.lstsq(A, f, rcond=None)

        if rank != 3:
            return _warn_or_msg(
                "Input does not contain enough significant data points.",
                warn_only=warn_only,
            )

        center = C[0:2]
        distances = spatial.minkowski_distance(center, data)
        r = np.sqrt(np.mean(distances**2))

        # Revert normalization and set init params.
        self.center = center * scale + origin
        self.radius = r * scale
        return None

    def residuals(self, data):
        """Determine residuals of data to model.

        For each point the shortest distance to the circle is returned.

        Parameters
        ----------
        data : (N, 2) array
            N points with ``(x, y)`` coordinates, respectively.

        Returns
        -------
        residuals : (N,) array
            Residual for each data point.

        """

        _check_data_dim(data, dim=2)

        xc, yc = self.center
        r = self.radius

        x = data[:, 0]
        y = data[:, 1]

        return r - np.sqrt((x - xc) ** 2 + (y - yc) ** 2)

    @_deprecate_model_params
    def predict_xy(self, t, params=DEPRECATED):
        """Predict x- and y-coordinates using the estimated model.

        Parameters
        ----------
        t : array-like
            Angles in circle in radians. Angles start to count from positive
            x-axis to positive y-axis in a right-handed system.

        Returns
        -------
        xy : (..., 2) array
            Predicted x- and y-coordinates.

        Other parameters
        ----------------
        params : `~.DEPRECATED`, optional
            Optional parameters ``xc``, ``yc``, `radius`.

            .. deprecated:: {{ start_version }}
        """
        t = np.asanyarray(t)
        (xc, yc), r = self._get_init_values(params)

        x = xc + r * np.cos(t)
        y = yc + r * np.sin(t)

        return np.concatenate((x[..., None], y[..., None]), axis=t.ndim)

    @_deprecate_estimate
    def estimate(self, data):
        """Estimate circle model from data using total least squares.

        Parameters
        ----------
        data : (N, 2) array
            N points with ``(x, y)`` coordinates, respectively.

        Returns
        -------
        success : bool
            True, if model estimation succeeds.

        """
        return self._estimate(data) is None


@_deprecate_no_args
class EllipseModel(_BaseModel):
    """Total least squares estimator for 2D ellipses.

    The functional model of the ellipse is::

        xt = xc + a*cos(theta)*cos(t) - b*sin(theta)*sin(t)
        yt = yc + a*sin(theta)*cos(t) + b*cos(theta)*sin(t)
        d = sqrt((x - xt)**2 + (y - yt)**2)

    where ``(xt, yt)`` is the closest point on the ellipse to ``(x, y)``. Thus
    d is the shortest distance from the point to the ellipse.

    The estimator is based on a least squares minimization. The optimal
    solution is computed directly, no iterations are required. This leads
    to a simple, stable and robust fitting method.

    Parameters
    ----------
    center : array-like, shape (2,)
        Coordinates of ellipse center.
    axis_lengths : array-like, shape (2,)
        Length of first axis and length of second axis.  Call these ``a`` and
        ``b``.
    theta : float
        Angle of first axis.

    Raises
    ------
    ValueError
        If `center` does not have length 2.

    Examples
    --------

    >>> em = EllipseModel((10, 15), (8, 4), np.deg2rad(30))
    >>> xy = em.predict_xy(np.linspace(0, 2 * np.pi, 25))
    >>> ellipse = EllipseModel.from_estimate(xy)
    >>> ellipse.center
    array([10., 15.])
    >>> ellipse.axis_lengths
    array([8., 4.])
    >>> round(ellipse.theta, 2)
    0.52
    >>> np.round(abs(ellipse.residuals(xy)), 5)
    array([0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.,
           0., 0., 0., 0., 0., 0., 0., 0.])

    The estimation can fail when — for example — all the input or output
    points are the same.  If this happens, you will get an ellipse model for
    which ``bool(model)`` is ``False``:

    >>> # A successfully estimated model is truthy:
    >>> if ellipse:
    ...     print("Estimation succeeded.")
    Estimation succeeded.
    >>> # Not so for a degenerate model with identical points.
    >>> bad_data = np.ones((4, 2))
    >>> bad_ellipse = EllipseModel.from_estimate(bad_data)
    >>> if not bad_ellipse:
    ...     print("Estimation failed.")
    Estimation failed.

    Trying to use this failed estimation transform result will give a suitable
    error:

    >>> bad_ellipse.residuals(xy)  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
      ...
    FailedEstimationAccessError: No attribute "residuals" for failed estimation ...
    """

    def _args_init(self, center, axis_lengths, theta):
        """Initialize ``EllipseModel`` instance.

        Parameters
        ----------
        center : array-like, shape (2,)
            Coordinates of ellipse center.
        axis_lengths : array-like, shape (2,)
            Length of first axis and length of second axis.  Call these ``a``
            and ``b``.
        theta : float
            Angle of first axis.
        """
        self.center, self.axis_lengths, self.theta = self._check_init_values(
            center, axis_lengths, theta
        )

    def _check_init_values(self, center, axis_lengths, theta):
        center, axis_lengths = [np.array(v) for v in (center, axis_lengths)]
        if not len(center) == 2:
            raise ValueError('Center coordinates should be length 2')
        if not len(axis_lengths) == 2:
            raise ValueError('Axis lengths should be length 2')
        return center, axis_lengths, theta

    def _params2init_values(self, params):
        params = np.array(params)
        if len(params) != 5:
            raise ValueError('Input `params` should be length 5')
        return self._check_init_values(params[:2], params[2:4], params[4])

    @property
    @deprecate_func(
        deprecated_version=_PARAMS_DEP_START,
        removed_version=_PARAMS_DEP_STOP,
        hint='`params` attribute deprecated; use `center, axis_lengths, theta` attributes instead',
    )
    def params(self):
        """Return model attributes ``center, axis_lengths, theta`` as 1D array."""
        return np.r_[self.center, self.axis_lengths, self.theta]

    @classmethod
    def from_estimate(cls, data):
        """Estimate ellipse model from data using total least squares.

        Parameters
        ----------
        data : (N, 2) array
            N points with ``(x, y)`` coordinates, respectively.

        Returns
        -------
        model : Self or `~.FailedEstimation`
            An instance of the ellipse model if the estimation succeeded.
            Otherwise, we return a special ``FailedEstimation`` object to
            signal a failed estimation. Testing the truth value of the failed
            estimation object will return ``False``. E.g.

            .. code-block:: python

                model = EllipseModel.from_estimate(...)
                if not model:
                    raise RuntimeError(f"Failed estimation: {model}")

        References
        ----------
        .. [1] Halir, R.; Flusser, J. "Numerically stable direct least squares
               fitting of ellipses". In Proc. 6th International Conference in
               Central Europe on Computer Graphics and Visualization.
               WSCG (Vol. 98, pp. 125-132).

        """
        return super().from_estimate(data)

    def _estimate(self, data, warn_only=True):
        # Original Implementation: Ben Hammel, Nick Sullivan-Molina
        # another REFERENCE: [2] http://mathworld.wolfram.com/Ellipse.html
        _check_data_dim(data, dim=2)

        if len(data) < 5:
            return _warn_or_msg(
                "Need at least 5 data points to estimate an ellipse.",
                warn_only=warn_only,
            )

        # to prevent integer overflow, cast data to float, if it isn't already
        float_type = np.promote_types(data.dtype, np.float32)
        data = data.astype(float_type, copy=False)

        # normalize value range to avoid misfitting due to numeric errors if
        # the relative distances are small compared to absolute distances
        origin = data.mean(axis=0)
        data = data - origin
        scale = data.std()
        if scale < np.finfo(float_type).tiny:
            return _warn_or_msg(
                "Standard deviation of data is too small to estimate "
                "ellipse with meaningful precision.",
                warn_only=warn_only,
            )
        data /= scale

        x = data[:, 0]
        y = data[:, 1]

        # Quadratic part of design matrix [eqn. 15] from [1]
        D1 = np.vstack([x**2, x * y, y**2]).T
        # Linear part of design matrix [eqn. 16] from [1]
        D2 = np.vstack([x, y, np.ones_like(x)]).T

        # forming scatter matrix [eqn. 17] from [1]
        S1 = D1.T @ D1
        S2 = D1.T @ D2
        S3 = D2.T @ D2

        # Constraint matrix [eqn. 18]
        C1 = np.array([[0.0, 0.0, 2.0], [0.0, -1.0, 0.0], [2.0, 0.0, 0.0]])

        try:
            # Reduced scatter matrix [eqn. 29]
            M = inv(C1) @ (S1 - S2 @ inv(S3) @ S2.T)
        except np.linalg.LinAlgError:  # LinAlgError: Singular matrix
            return 'Singular matrix from estimation'

        # M*|a b c >=l|a b c >. Find eigenvalues and eigenvectors
        # from this equation [eqn. 28]
        eig_vals, eig_vecs = np.linalg.eig(M)

        # eigenvector must meet constraint 4ac - b^2 to be valid.
        cond = 4 * np.multiply(eig_vecs[0, :], eig_vecs[2, :]) - np.power(
            eig_vecs[1, :], 2
        )
        a1 = eig_vecs[:, (cond > 0)]
        # seeks for empty matrix
        if 0 in a1.shape or len(a1.ravel()) != 3:
            return 'Eigenvector constraints not met'
        a, b, c = a1.ravel()

        # |d f g> = -S3^(-1)*S2^(T)*|a b c> [eqn. 24]
        a2 = -inv(S3) @ S2.T @ a1
        d, f, g = a2.ravel()

        # eigenvectors are the coefficients of an ellipse in general form
        # a*x^2 + 2*b*x*y + c*y^2 + 2*d*x + 2*f*y + g = 0 (eqn. 15) from [2]
        b /= 2.0
        d /= 2.0
        f /= 2.0

        # finding center of ellipse [eqn.19 and 20] from [2]
        x0 = (c * d - b * f) / (b**2.0 - a * c)
        y0 = (a * f - b * d) / (b**2.0 - a * c)

        # Find the semi-axes lengths [eqn. 21 and 22] from [2]
        numerator = a * f**2 + c * d**2 + g * b**2 - 2 * b * d * f - a * c * g
        term = np.sqrt((a - c) ** 2 + 4 * b**2)
        denominator1 = (b**2 - a * c) * (term - (a + c))
        denominator2 = (b**2 - a * c) * (-term - (a + c))
        width = np.sqrt(2 * numerator / denominator1)
        height = np.sqrt(2 * numerator / denominator2)

        # angle of counterclockwise rotation of major-axis of ellipse
        # to x-axis [eqn. 23] from [2].
        phi = 0.5 * np.arctan((2.0 * b) / (a - c))
        if a > c:
            phi += 0.5 * np.pi

        # stabilize parameters:
        # sometimes small fluctuations in data can cause
        # height and width to swap
        if width < height:
            width, height = height, width
            phi += np.pi / 2

        phi %= np.pi

        # Revert normalization and set parameters.
        params = np.nan_to_num([x0, y0, width, height, phi]).real
        params[:4] *= scale
        params[:2] += origin

        self.center, self.axis_lengths, self.theta = (
            params[:2],
            params[2:4],
            params[-1],
        )
        return None

    def residuals(self, data):
        """Determine residuals of data to model.

        For each point the shortest distance to the ellipse is returned.

        Parameters
        ----------
        data : (N, 2) array
            N points with ``(x, y)`` coordinates, respectively.

        Returns
        -------
        residuals : (N,) array
            Residual for each data point.

        """

        _check_data_dim(data, dim=2)

        xc, yc = self.center
        a, b = self.axis_lengths
        theta = self.theta

        ctheta = math.cos(theta)
        stheta = math.sin(theta)

        x = data[:, 0]
        y = data[:, 1]

        N = data.shape[0]

        def fun(t, xi, yi):
            ct = math.cos(np.squeeze(t))
            st = math.sin(np.squeeze(t))
            xt = xc + a * ctheta * ct - b * stheta * st
            yt = yc + a * stheta * ct + b * ctheta * st
            return (xi - xt) ** 2 + (yi - yt) ** 2

        # def Dfun(t, xi, yi):
        #     ct = math.cos(t)
        #     st = math.sin(t)
        #     xt = xc + a * ctheta * ct - b * stheta * st
        #     yt = yc + a * stheta * ct + b * ctheta * st
        #     dfx_t = - 2 * (xi - xt) * (- a * ctheta * st
        #                                - b * stheta * ct)
        #     dfy_t = - 2 * (yi - yt) * (- a * stheta * st
        #                                + b * ctheta * ct)
        #     return [dfx_t + dfy_t]

        residuals = np.empty((N,), dtype=np.float64)

        # initial guess for parameter t of closest point on ellipse
        t0 = np.arctan2(y - yc, x - xc) - theta

        # determine shortest distance to ellipse for each point
        for i in range(N):
            xi = x[i]
            yi = y[i]
            # faster without Dfun, because of the python overhead
            t, _ = optimize.leastsq(fun, t0[i], args=(xi, yi))
            residuals[i] = np.sqrt(fun(t, xi, yi))

        return residuals

    @_deprecate_model_params
    def predict_xy(self, t, params=DEPRECATED):
        """Predict x- and y-coordinates using the estimated model.

        Parameters
        ----------
        t : array
            Angles in circle in radians. Angles start to count from positive
            x-axis to positive y-axis in a right-handed system.

        Returns
        -------
        xy : (..., 2) array
            Predicted x- and y-coordinates.

        Other parameters
        ----------------
        params : `~.DEPRECATED`, optional
            Optional ellipse model parameters in the following order ``xc``,
            ``yc``, `a`, `b`, `theta`.

            .. deprecated:: {{ start_version }}
        """
        t = np.asanyarray(t)
        (xc, yc), (a, b), theta = self._get_init_values(params)

        ct = np.cos(t)
        st = np.sin(t)
        ctheta = math.cos(theta)
        stheta = math.sin(theta)

        x = xc + a * ctheta * ct - b * stheta * st
        y = yc + a * stheta * ct + b * ctheta * st

        return np.concatenate((x[..., None], y[..., None]), axis=t.ndim)

    @_deprecate_estimate
    def estimate(self, data):
        """Estimate ellipse model from data using total least squares.

        Parameters
        ----------
        data : (N, 2) array
            N points with ``(x, y)`` coordinates, respectively.

        Returns
        -------
        success : bool
            True, if model estimation succeeds.


        References
        ----------
        .. [1] Halir, R.; Flusser, J. "Numerically stable direct least squares
               fitting of ellipses". In Proc. 6th International Conference in
               Central Europe on Computer Graphics and Visualization.
               WSCG (Vol. 98, pp. 125-132).

        """
        return self._estimate(data) is None


def _dynamic_max_trials(n_inliers, n_samples, min_samples, probability):
    """Determine number trials such that at least one outlier-free subset is
    sampled for the given inlier/outlier ratio.

    Parameters
    ----------
    n_inliers : int
        Number of inliers in the data.
    n_samples : int
        Total number of samples in the data.
    min_samples : int
        Minimum number of samples chosen randomly from original data.
    probability : float
        Probability (confidence) that one outlier-free sample is generated.

    Returns
    -------
    trials : int
        Number of trials.
    """
    if probability == 0:
        return 0
    if n_inliers == 0:
        return np.inf
    inlier_ratio = n_inliers / n_samples
    nom = 1 - probability
    denom = 1 - inlier_ratio**min_samples
    # Keep (de-)nominator in the range of [_EPSILON, 1 - _EPSILON] so that
    # it is always guaranteed that the logarithm is negative and we return
    # a positive number of trials.
    nom = np.clip(nom, a_min=_EPSILON, a_max=1 - _EPSILON)
    denom = np.clip(denom, a_min=_EPSILON, a_max=1 - _EPSILON)
    return np.ceil(np.log(nom) / np.log(denom))


def add_from_estimate(cls):
    """Add ``from_estimate`` method  class using ``estimate`` method"""

    if hasattr(cls, 'from_estimate'):
        if not inspect.ismethod(cls.from_estimate):
            raise TypeError(f'Class {cls} `from_estimate` must be a ' 'class method.')
        return cls

    if not hasattr(cls, 'estimate'):
        raise TypeError(
            f'Class {cls} must have `from_estimate` class method '
            'or `estimate` method.'
        )

    warn(
        "Passing custom classes without `from_estimate` has been deprecated "
        "since version 0.26 and will be removed in version 2.2. "
        "Add `from_estimate` class method to custom class to avoid this "
        "warning.",
        category=FutureWarning,
        stacklevel=3,
    )

    class FromEstimated(cls):
        @classmethod
        def from_estimate(klass, *args, **kwargs):
            # Assume we can make default instance without input arguments.
            instance = klass()
            success = instance.estimate(*args, **kwargs)
            return (
                instance
                if success
                else FailedEstimation(f'`{cls.__name__}` estimation failed')
            )

    return FromEstimated


def ransac(
    data,
    model_class,
    min_samples,
    residual_threshold,
    is_data_valid=None,
    is_model_valid=None,
    max_trials=100,
    stop_sample_num=np.inf,
    stop_residuals_sum=0,
    stop_probability=1,
    rng=None,
    initial_inliers=None,
):
    """Fit a model to data with the RANSAC (random sample consensus) algorithm.

    RANSAC is an iterative algorithm for the robust estimation of parameters
    from a subset of inliers from the complete data set. Each iteration
    performs the following tasks:

    1. Select `min_samples` random samples from the original data and check
       whether the set of data is valid (see `is_data_valid`).
    2. Estimate a model to the random subset
       (`model_cls.from_estimate(*data[random_subset]`) and check whether the
       estimated model is valid (see `is_model_valid`).
    3. Classify all data as inliers or outliers by calculating the residuals
       to the estimated model (`model_cls.residuals(*data)`) - all data samples
       with residuals smaller than the `residual_threshold` are considered as
       inliers.
    4. Save estimated model as best model if number of inlier samples is
       maximal. In case the current estimated model has the same number of
       inliers, it is only considered as the best model if it has less sum of
       residuals.

    These steps are performed either a maximum number of times or until one of
    the special stop criteria are met. The final model is estimated using all
    inlier samples of the previously determined best model.

    Parameters
    ----------
    data : list or tuple or array of shape (N,)
        Data set to which the model is fitted, where N is the number of data
        points and the remaining dimension are depending on model requirements.
        If the model class requires multiple input data arrays (e.g. source and
        destination coordinates of  ``skimage.transform.AffineTransform``),
        they can be optionally passed as tuple or list. Note, that in this case
        the functions ``estimate(*data)``, ``residuals(*data)``,
        ``is_model_valid(model, *random_data)`` and
        ``is_data_valid(*random_data)`` must all take each data array as
        separate arguments.
    model_class : type
        Class with the following methods:

        * Either:

          * ``from_estimate`` class method returning transform instance, as in
            ``tform = model_class.from_estimate(*data)``; the resulting
            ``tform`` should be truthy (``bool(tform) == True``) where
            estimation succeeded, or falsey (``bool(tform) == False``) where it
            failed;  OR
          * (deprecated) ``estimate`` instance method, returning flag to
            indicate successful estimation, as in ``tform = model_class();
            success = tform.estimate(*data)``. ``success == True`` when
            estimation succeeded, ``success == False`` when it failed.

        * ``residuals(*data)``

        Your model should conform to the ``RansacModelProtocol`` — meaning
        implement all of the methods / attributes specified by the
        :class:``RansacModelProctocol``. An easy check to see whether that is
        the case is to use ``isinstance(MyModel, RansacModelProtocol)``. See
        https://docs.python.org/3/library/typing.html#typing.Protocol for more
        details.

    min_samples : int, in range (0, N)
        The minimum number of data points to fit a model to.
    residual_threshold : float, >0
        Maximum distance for a data point to be classified as an inlier.
    is_data_valid : Callable, optional
        This function is called with the randomly selected data before the
        model is fitted to it: `is_data_valid(*random_data)`.
    is_model_valid : Callable, optional
        This function is called with the estimated model and the randomly
        selected data: `is_model_valid(model, *random_data)`, .
    max_trials : int, optional
        Maximum number of iterations for random sample selection.
    stop_sample_num : int, optional
        Stop iteration if at least this number of inliers are found.
    stop_residuals_sum : float, optional
        Stop iteration if sum of residuals is less than or equal to this
        threshold.
    stop_probability : float, optional, in range [0, 1]
        RANSAC iteration stops if at least one outlier-free set of the
        training data is sampled with ``probability >= stop_probability``,
        depending on the current best model's inlier ratio and the number
        of trials. This requires to generate at least N samples (trials):

            N >= log(1 - probability) / log(1 - e**m)

        where the probability (confidence) is typically set to a high value
        such as 0.99, e is the current fraction of inliers w.r.t. the
        total number of samples, and m is the min_samples value.
    rng : {`numpy.random.Generator`, int}, optional
        Pseudo-random number generator.
        By default, a PCG64 generator is used (see :func:`numpy.random.default_rng`).
        If `rng` is an int, it is used to seed the generator.
    initial_inliers : array-like of bool, shape (N,), optional
        Initial samples selection for model estimation


    Returns
    -------
    model : object
        Best model with largest consensus set.
    inliers : (N,) array
        Boolean mask of inliers classified as ``True``.

    References
    ----------
    .. [1] "RANSAC", Wikipedia, https://en.wikipedia.org/wiki/RANSAC

    Examples
    --------

    Generate ellipse data without tilt and add noise:

    >>> t = np.linspace(0, 2 * np.pi, 50)
    >>> xc, yc = 20, 30
    >>> a, b = 5, 10
    >>> x = xc + a * np.cos(t)
    >>> y = yc + b * np.sin(t)
    >>> data = np.column_stack([x, y])
    >>> rng = np.random.default_rng(203560)  # do not copy this value
    >>> data += rng.normal(size=data.shape)

    Add some faulty data:

    >>> data[0] = (100, 100)
    >>> data[1] = (110, 120)
    >>> data[2] = (120, 130)
    >>> data[3] = (140, 130)

    Estimate ellipse model using all available data:

    >>> model = EllipseModel.from_estimate(data)
    >>> np.round(model.center)
    array([71., 75.])
    >>> np.round(model.axis_lengths)
    array([77., 13.])
    >>> np.round(model.theta)
    1.0

    Next we estimate an ellipse model using RANSAC.

    Note that the results are not deterministic, because the RANSAC algorithm
    uses some randomness.   If you need the results to be deterministic, pass a
    seeded number generator with the ``rng`` argument to ``ransac``.

    >>> ransac_model, inliers = ransac(data, EllipseModel, 20, 3, max_trials=50)
    >>> np.abs(np.round(ransac_model.center))  # doctest: +SKIP
    array([20., 30.])
    >>> np.abs(np.round(ransac_model.axis_lengths))  # doctest: +SKIP
    array([10., 6.])
    >>> np.abs(np.round(ransac_model.theta))    # doctest: +SKIP
    2.0
    >>> inliers  # doctest: +SKIP
    array([False, False, False, False,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True], dtype=bool)
    >>> sum(inliers) > 40
    True

    RANSAC can be used to robustly estimate a geometric
    transformation. In this section, we also show how to use a
    proportion of the total samples, rather than an absolute number.

    >>> from skimage.transform import SimilarityTransform
    >>> rng = np.random.default_rng()
    >>> src = 100 * rng.random((50, 2))
    >>> model0 = SimilarityTransform(scale=0.5, rotation=1,
    ...                              translation=(10, 20))
    >>> dst = model0(src)
    >>> dst[0] = (10000, 10000)
    >>> dst[1] = (-100, 100)
    >>> dst[2] = (50, 50)
    >>> ratio = 0.5  # use half of the samples
    >>> min_samples = int(ratio * len(src))
    >>> model, inliers = ransac(
    ...     (src, dst),
    ...     SimilarityTransform,
    ...     min_samples,
    ...     10,
    ...     initial_inliers=np.ones(len(src), dtype=bool),
    ... )  # doctest: +SKIP
    >>> inliers  # doctest: +SKIP
    array([False, False, False,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True,  True,  True,  True,  True,
            True,  True,  True,  True,  True])

    """

    best_inlier_num = 0
    best_inlier_residuals_sum = np.inf
    best_inliers = []
    validate_model = is_model_valid is not None
    validate_data = is_data_valid is not None

    rng = np.random.default_rng(rng)

    # in case data is not pair of input and output, male it like it
    if not isinstance(data, (tuple, list)):
        data = (data,)
    num_samples = len(data[0])

    if not (0 < min_samples <= num_samples):
        raise ValueError(f"`min_samples` must be in range (0, {num_samples}]")

    if residual_threshold < 0:
        raise ValueError("`residual_threshold` must be greater than zero")

    if max_trials < 0:
        raise ValueError("`max_trials` must be greater than zero")

    if not (0 <= stop_probability <= 1):
        raise ValueError("`stop_probability` must be in range [0, 1]")

    if initial_inliers is not None and len(initial_inliers) != num_samples:
        raise ValueError(
            f"RANSAC received a vector of initial inliers (length "
            f"{len(initial_inliers)}) that didn't match the number of "
            f"samples ({num_samples}). The vector of initial inliers should "
            f"have the same length as the number of samples and contain only "
            f"True (this sample is an initial inlier) and False (this one "
            f"isn't) values."
        )

    # for the first run use initial guess of inliers
    spl_idxs = (
        initial_inliers
        if initial_inliers is not None
        else rng.choice(num_samples, min_samples, replace=False)
    )

    # Ensure model_class has from_estimate class method.
    model_class = add_from_estimate(model_class)

    # Check protocol.
    if not isinstance(model_class, RansacModelProtocol):
        raise TypeError(
            f"`model_class` {model_class} should be of (protocol) type "
            "RansacModelProtocol"
        )

    num_trials = 0
    # max_trials can be updated inside the loop, so this cannot be a for-loop
    while num_trials < max_trials:
        num_trials += 1

        # do sample selection according data pairs
        samples = [d[spl_idxs] for d in data]

        # for next iteration choose random sample set and be sure that
        # no samples repeat
        spl_idxs = rng.choice(num_samples, min_samples, replace=False)

        # optional check if random sample set is valid
        if validate_data and not is_data_valid(*samples):
            continue

        model = model_class.from_estimate(*samples)
        # backwards compatibility
        if not model:
            continue

        # optional check if estimated model is valid
        if validate_model and not is_model_valid(model, *samples):
            continue

        residuals = np.abs(model.residuals(*data))
        # consensus set / inliers
        inliers = residuals < residual_threshold
        residuals_sum = residuals.dot(residuals)

        # choose as new best model if number of inliers is maximal
        inliers_count = np.count_nonzero(inliers)
        if (
            # more inliers
            inliers_count > best_inlier_num
            # same number of inliers but less "error" in terms of residuals
            or (
                inliers_count == best_inlier_num
                and residuals_sum < best_inlier_residuals_sum
            )
        ):
            best_inlier_num = inliers_count
            best_inlier_residuals_sum = residuals_sum
            best_inliers = inliers
            max_trials = min(
                max_trials,
                _dynamic_max_trials(
                    best_inlier_num, num_samples, min_samples, stop_probability
                ),
            )
            if (
                best_inlier_num >= stop_sample_num
                or best_inlier_residuals_sum <= stop_residuals_sum
            ):
                break

    # estimate final model using all inliers
    if any(best_inliers):
        # select inliers for each data array
        data_inliers = [d[best_inliers] for d in data]
        model = model_class.from_estimate(*data_inliers)
        if validate_model and not is_model_valid(model, *data_inliers):
            warn("Estimated model is not valid. Try increasing max_trials.")
    else:
        model = None
        best_inliers = None
        warn("No inliers found. Model not fitted")

    # Return model from wrapper, otherwise model itself.
    return getattr(model, 'model', model), best_inliers
