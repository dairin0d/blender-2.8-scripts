#  ***** BEGIN GPL LICENSE BLOCK *****
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#  ***** END GPL LICENSE BLOCK *****

import math
import numbers
import itertools

from mathutils import Color, Vector, Euler, Quaternion, Matrix

import numpy as np

from .utils_math import divide, nan_min, nan_max, replace_nan

#============================================================================#

isnan = math.isnan

Number = numbers.Number

np_asarray = np.asarray
np_array = np.array
np_full = np.full
np_ndarray = np.ndarray

def _sync_len(a, b, default=math.nan):
    # a, b: any array-like or scalar
    # scalars are converted to 1-item arrays
    # arrays are resized to max(len(a), len(b))
    
    a = np_asarray(a, float)
    if not a.shape: a.resize(1, refcheck=False)
    na = len(a)
    
    b = np_asarray(b, float)
    if not b.shape: b.resize(1, refcheck=False)
    nb = len(b)
    
    if na < nb:
        a = np.resize(a, nb, refcheck=False)
        a[na:] = default
    elif nb < na:
        b = np.resize(b, na, refcheck=False)
        b[nb:] = default
    
    return a, b

_axis_name_map = {
    "x":0, "X":0,
    "y":1, "Y":1,
    "z":2, "Z":2,
}

class RangeAggregator:
    __slots__ = ["_convert", "_dimension", "_min", "_max", "add"]
    
    dimension = property(lambda self: self._dimension)
    min = property(lambda self: self._convert(self._min))
    max = property(lambda self: self._convert(self._max))
    
    def __bool__(self):
        return any(not isnan(v) for v in self._min)
    
    def __len__(self):
        return 2
    
    def __getitem__(self, index):
        if index == 0:
            return self.min
        elif index == 1:
            return self.max
        else:
            raise IndexError(f"Invalid index {index}")
    
    def __repr__(self):
        return f"{self._min} .. {self._max}"
    
    def __init__(self, dimension=None, convert=None, nan=None):
        if nan is not None:
            if convert:
                self._convert = (lambda value: convert(replace_nan(value, nan)))
            else:
                self._convert = (lambda value: replace_nan(value, nan))
        else:
            self._convert = convert or (lambda value: value)
        
        if (dimension is None) or (dimension <= 0):
            self._dimension = 0
            self._min = []
            self._max = []
            self.add = self.__add_dynamic
        else:
            self._dimension = dimension
            self._min = [math.nan] * dimension
            self._max = [math.nan] * dimension
            self.add = self.__add_static
    
    def __add_static(self, point):
        _min = self._min
        _max = self._max
        
        for i, v in enumerate(point):
            if not (_min[i] <= v): _min[i] = v
            if not (_max[i] >= v): _max[i] = v
    
    def __add_dynamic(self, point):
        if hasattr(point, "__len__"):
            try:
                n = len(point)
            except TypeError:
                return # non-collection or 0-dimensional numpy array
            
            d = self._dimension
            _min = self._min
            _max = self._max
            
            if n > d:
                self._dimension = n
                for i in range(d, n):
                    v = point[i]
                    if v is None: v = math.nan
                    _min.append(v)
                    _max.append(v)
            
            for i in range(min(d, n)):
                v = point[i]
                if v is None: continue
                if not (_min[i] <= v): _min[i] = v
                if not (_max[i] >= v): _max[i] = v
        elif hasattr(point, "__iter__"):
            try:
                point = iter(point)
            except TypeError:
                return # non-iterable or 0-dimensional numpy array
            
            d = self._dimension
            _min = self._min
            _max = self._max
            
            for i, v in enumerate(point):
                if i >= d:
                    _min.append(math.nan)
                    _max.append(math.nan)
                    d += 1
                    self._dimension = d
                
                if v is None: continue
                if not (_min[i] <= v): _min[i] = v
                if not (_max[i] >= v): _max[i] = v
    
    def update(self, points):
        try:
            points = iter(points)
        except TypeError:
            return # non-iterable or 0-dimensional numpy array
        
        for point in points:
            self.add(point)
    
    def __call__(self, points):
        if isinstance(points, Bounds):
            self.add(points.min)
            self.add(points.max)
        elif hasattr(points, "__len__"):
            try:
                if len(points) == 0: return self
            except TypeError:
                return self # non-collection or 0-dimensional numpy array
            
            if isinstance(points[0], Number):
                self.add(points)
            else:
                for point in points:
                    self.add(point)
        elif hasattr(points, "__iter__"):
            try:
                points = iter(points)
            except TypeError:
                return # non-iterable or 0-dimensional numpy array
            
            for point in points:
                self.add(point)
        
        return self

class Bounds:
    @classmethod
    def zero(cls, dimension, type="MinSize"):
        bounds = cls._instantiate_type(type)
        bounds.set_min_size(np.zeros(dimension), np.zeros(dimension))
        return bounds
    
    @classmethod
    def one(cls, dimension, type="MinSize"):
        bounds = cls._instantiate_type(type)
        bounds.set_min_size(np.zeros(dimension), np.ones(dimension))
        return bounds
    
    def __new__(cls, source=None, dimension=None, **kwargs):
        """
        Bounds([type], kwarg1=..., kwarg2=...) - initialize from the given kwargs
        Bounds(bounds, [type=..., kwarg=...]) - copy + set the given kwarg
        Bounds(iterable, [type=..., kwarg=...]) - bounds aggregation
        """
        
        type = kwargs.pop("type", None)
        
        if (source is None) or isinstance(source, str):
            bounds = cls._instantiate_type(type or source or cls.__kwargs_detect_type(kwargs))
        elif isinstance(source, Bounds):
            bounds = source.copy(type or source.type)
        else: # iterable is expected
            bounds = cls._instantiate_type(type or "MinMax")
            bounds.encapsulate(source)
        
        if kwargs: bounds.set(**kwargs)
        
        if dimension: bounds.dimension = dimension
        
        return bounds
    
    __type_name_map = {}
    
    @classmethod
    def _register(cls, type_class):
        name = type_class.__name__
        cls.__type_name_map[name] = type_class
        setattr(cls, name, type_class)
    
    @classmethod
    def _instantiate_type(cls, type):
        type_class = cls.__type_name_map.get(type, None)
        if type_class: return type_class()
        raise ValueError(f"Unrecognized Bounds type: {type}")
    
    @classmethod
    def __kwargs_detect_type(cls, kwargs):
        has_min = "min" in kwargs
        has_max = "max" in kwargs
        if has_min and has_max: return "MinMax"
        if has_min: return "MinSize"
        if has_max: return "MaxSize"
        return "CenterSize"
    
    def __override_center_extents(bounds, kwargs):
        center, extents = _sync_len(kwargs["center"], kwargs["extents"])
        bounds.set_center_size(center, extents * 2)
    
    def __override_center_max(bounds, kwargs):
        center, max = _sync_len(kwargs["center"], kwargs["max"])
        bounds.set_center_size(center, (max - center) * 2)
    
    def __override_center_min(bounds, kwargs):
        center, min = _sync_len(kwargs["center"], kwargs["min"])
        bounds.set_center_size(center, (center - min) * 2)
    
    def __override_center_size(bounds, kwargs):
        bounds.set_center_size(kwargs["center"], kwargs["size"])
    
    def __override_extents_max(bounds, kwargs):
        extents, max = _sync_len(kwargs["extents"], kwargs["max"])
        bounds.set_max_size(max, extents * 2)
    
    def __override_extents_min(bounds, kwargs):
        extents, min = _sync_len(kwargs["extents"], kwargs["min"])
        bounds.set_min_size(min, extents * 2)
    
    def __override_max_min(bounds, kwargs):
        bounds.set_min_max(kwargs["min"], kwargs["max"])
    
    def __override_max_size(bounds, kwargs):
        bounds.set_max_size(kwargs["max"], kwargs["size"])
    
    def __override_min_size(bounds, kwargs):
        bounds.set_min_size(kwargs["min"], kwargs["size"])
    
    # Note: elements inside keys are sorted
    __override_map = {
        ("center", "extents"):__override_center_extents,
        ("center", "max"):__override_center_max,
        ("center", "min"):__override_center_min,
        ("center", "size"):__override_center_size,
        ("extents", "max"):__override_extents_max,
        ("extents", "min"):__override_extents_min,
        ("max", "min"):__override_max_min,
        ("max", "size"):__override_max_size,
        ("min", "size"):__override_min_size,
    }
    __override_attrs = {"center", "extents", "max", "min", "size"}
    
    #################################################################
    
    type = property(lambda self: self.__class__.__name__)
    
    def _get(self): return self.size * 0.5
    def _set(self, value): self.size = np_asarray(value) * 2
    extents = property(_get, _set)
    
    def _get(self):
        min, max = self.min, self.max
        return np.minimum(min, max), np.maximum(min, max)
    abs_range = property(_get)
    
    abs_size = property(lambda self: np.abs(self.size))
    
    volume = property(lambda self: np.product(self.size))
    
    def _get(self): return np_asarray([self._is_flipped(i) for i in range(self._len)])
    def _set(self, value):
        n = self._len
        for i, i_value in enumerate(value):
            if i >= n: break
            if (i_value is not None) and (i_value != self._is_flipped(i)): self._flip(i)
    flipped_axes = property(_get, _set)
    
    def _get(self): return any(self._is_flipped(i) for i in range(self._len))
    def _set(self, value): self.flipped_axes = (value for i in range(self._len))
    is_flipped = property(_get, _set)
    
    is_defined = property(lambda self: (self._len > 0) and not self.any_nan)
    
    del _get, _set
    
    def _convert(self, vec, default=math.nan, dtype=None):
        if vec is None: return None
        
        if isinstance(vec, Number):
            return np.resize(np_array(vec, dtype), self._len)
        
        vec = np_asarray(vec, dtype)
        n = self._len
        nvec = len(vec)
        
        if nvec == n:
            return vec
        elif nvec > n:
            return vec[:n]
        else:
            res = np_full(n, default, float)
            res[:nvec] = vec
            return res
    
    def _assign(self, a, b, default=math.nan):
        # a, b: any array-like or scalar
        # scalars are converted to 1-item arrays
        # arrays are resized to max(len(a), len(b))
        
        a = np_array(a, float)
        if not a.shape: a.resize(1, refcheck=False)
        na = len(a)
        
        b = np_array(b, float)
        if not b.shape: b.resize(1, refcheck=False)
        nb = len(b)
        
        if na < nb:
            a.resize(nb, refcheck=False)
            a[na:] = default
        elif nb < na:
            b.resize(na, refcheck=False)
            b[nb:] = default
        
        self._len = na
        
        # return slices to prevent accidental resizing
        return a[:], b[:]
    
    def _resize(self, a, b, n, default=math.nan):
        if self._len == n: return a, b
        na = min(n, len(a))
        nb = min(n, len(b))
        data = np_full((2, n), default, float)
        data[0][:na] = a[:na]
        data[1][:nb] = b[:nb]
        return data
    
    def __len__(self):
        return 2
    
    def __bool__(self):
        return self._len > 0
    
    def get(self, *args, convert=None, copy=False, nan=None):
        if not args: return None
        
        # If we try to assign wrapped convert to the same
        # variable, there will be infinite recursion
        if nan is not None:
            if convert:
                _convert = (lambda v: convert(replace_nan(v, nan)))
            else:
                _convert = (lambda v: replace_nan(v, nan))
        elif copy:
            _convert = ((lambda v: convert(np_array(v))) if convert else np_array)
        else:
            _convert = convert
        
        if len(args) == 1:
            if _convert: return _convert(getattr(self, args[0]))
            return getattr(self, args[0])
        
        if _convert: return tuple(_convert(getattr(self, arg)) for arg in args)
        
        return tuple(getattr(self, arg) for arg in args)
    
    def set(self, **kwargs):
        count = len(kwargs)
        
        if count == 1:
            key, value = next(iter(kwargs.items()))
            
            if key not in self.__override_attrs:
                raise TypeError(f"Unsupported override {key}")
            
            setattr(self, key, value)
        elif count == 2:
            map_key = tuple(kwargs.keys())
            if map_key[0] > map_key[1]: map_key = (map_key[1], map_key[0])
            
            overrider = self.__override_map.get(map_key)
            
            if not overrider:
                raise TypeError(f"Unsupported override combination {map_key}")
            
            overrider(self, kwargs)
        else:
            raise TypeError(f"Too many attribute overrides {tuple(kwargs.keys())}, only up to 2 are supported")
    
    __fix_type_map = {"min": "MinSize", "max": "MaxSize", "center": "CenterSize"}
    def resized(self, size, fix=None, type=None):
        # fix: min, max, center (which point should remain invariant)
        if (not type) and fix: type = self.__fix_type_map[fix]
        bounds = self.copy(type)
        if not fix:
            bounds.size = size
        else:
            bounds.set(size=size, **{fix: getattr(bounds, fix)})
        return bounds
    
    def moved(self, offset, type=None):
        bounds = self.copy(type)
        bounds.move(offset)
        return bounds
    
    def scaled(self, factor, origin=None, type=None):
        bounds = self.copy(type)
        bounds.scale(factor, origin)
        return bounds
    
    def grown(self, dmin, dmax=None, type=None):
        bounds = self.copy(type)
        bounds.grow(dmin, dmax)
        return bounds
    
    def shrunk(self, dmin, dmax=None, type=None):
        bounds = self.copy(type)
        bounds.shrink(dmin, dmax)
        return bounds
    
    def fitted(self, target, mode="min", type=None):
        bounds = self.copy(type or "CenterSize")
        bounds.fit(target, mode)
        return bounds
    
    def encapsulated(self, other, type=None):
        bounds = self.copy(type)
        bounds.encapsulate(other)
        return bounds
    
    def transformed(self, matrix, type=None):
        bounds = self.copy(type or "CenterSize")
        bounds.transform(matrix)
        return bounds
    
    def move(self, offset):
        offset = self._convert(offset, 0)
        self._move(offset)
    
    def scale(self, factor, origin=None):
        factor = self._convert(factor, 1)
        origin = self._convert(origin, 0)
        if origin is not None: self._move(-origin)
        self._scale(factor)
        if origin is not None: self._move(origin)
    
    def grow(self, dmin, dmax=None):
        dmin = self._convert(dmin, 0)
        dmax = (dmin if dmax is None else self._convert(dmax, 0))
        self._grow(dmin, dmax)
    
    def shrink(self, dmin, dmax=None):
        dmin = self._convert(dmin, 0)
        dmax = (dmin if dmax is None else self._convert(dmax, 0))
        self._grow(-dmin, -dmax)
    
    def fit(self, target, mode="min"):
        if isinstance(target, Bounds):
            size = self.__calc_fit_size(target.size, mode)
            center = self._convert(replace_nan(target.center, self.center))
            self.set_center_size(center, size)
        else: # a size vector
            self.size = self.__calc_fit_size(target, mode)
    
    def __calc_fit_size(self, target_size, mode):
        size = self.size
        scale = [divide(ts, s) for ts, s in zip(self._convert(target_size), size)]
        scale = [(s if math.isfinite(s) else 1.0) for s in scale]
        
        if isinstance(mode, int):
            scale = scale[mode]
            return (size if isnan(scale) else size * scale)
        elif mode == "min":
            return size * nan_min(scale, default=1.0)
        else:
            return size * nan_max(scale, default=1.0)
    
    def encapsulate(self, other):
        # Note: encapsulate() may increase the dimension
        if not other: return
        
        # Un-flip before obtaining min and max
        flipped_axes = self.flipped_axes
        is_flipped = np.any(flipped_axes)
        if is_flipped: self.is_flipped = False
        
        aggregator = RangeAggregator()
        encapsulate = aggregator.add
        
        encapsulate(self.min)
        encapsulate(self.max)
        
        if isinstance(other, Bounds):
            encapsulate(other.min)
            encapsulate(other.max)
        elif hasattr(other, "__getitem__") and isinstance(other[0], Number):
            encapsulate(other)
        else: # iterable is expected
            for item in other:
                if not item: continue
                if isinstance(item, Bounds):
                    encapsulate(item.min)
                    encapsulate(item.max)
                else:
                    encapsulate(item)
        
        self.set_min_max(aggregator.min, aggregator.max)
        
        if is_flipped: self.flipped_axes = flipped_axes
    
    def transform(self, matrix):
        # This trick was mentioned in the "Physics - Broad phase and Narrow phase" chapter of
        # Newcastle University » Game » Masters Degree » Game Technologies » Physics Tutorials
        # https://research.ncl.ac.uk/game/mastersdegree/gametechnologies/physicstutorials
        #   /6accelerationstructures/Physics%20-%20Spatial%20Acceleration%20Structures.pdf
        # Basically, take an abs() of all matrix values and transform local bbox half-size by it
        matrix_abs = Matrix([[abs(v) for v in row] for row in matrix.to_3x3()])
        center, extents = Vector(self.center), Vector(self.extents)
        extents = matrix_abs @ extents
        center = matrix @ center
        self.set_center_size(center, extents * 2)
    
    def split(self, mode, axis, value, clamp=False):
        if mode.startswith("min"):
            return self.split_min(axis, value, mode.endswith("_rel"), clamp)
        elif mode.startswith("max"):
            return self.split_max(axis, value, mode.endswith("_rel"), clamp)
        else:
            return self.split_abs(axis, value, clamp)
    
    def split_abs(self, axis, value, clamp=False):
        if isinstance(axis, str): axis = _axis_name_map[axis]
        return self.__split(self.min, self.max, axis, value, clamp)
    
    def split_min(self, axis, value, relative=False, clamp=False):
        if isinstance(axis, str): axis = _axis_name_map[axis]
        min, max = self.min, self.max
        if relative: value *= abs(max[axis] - min[axis])
        return self.__split(min, max, axis, min[axis] + value, clamp)
    
    def split_max(self, axis, value, relative=False, clamp=False):
        if isinstance(axis, str): axis = _axis_name_map[axis]
        min, max = self.min, self.max
        if relative: value *= abs(max[axis] - min[axis])
        return self.__split(min, max, axis, max[axis] - value, clamp)
    
    def __split(self, _min, _max, axis, value, clamp=False):
        if clamp: value = min(max(value, _min[axis]), _max[axis])
        smin, smax = _min.copy(), _max.copy()
        smin[axis] = smax[axis] = value
        return MinMax(_min, smax), MinMax(smin, _max)
    
    def __intersect(self, abs_min, abs_max, other, return_flipped=False):
        other_min, other_max = other.abs_range
        items = itertools.zip_longest(abs_min, abs_max, other_min, other_max, fillvalue = math.nan)
        res_min, res_max = [], []
        for a_min, a_max, b_min, b_max in items:
            res_min.append(nan_max(a_min, b_min))
            res_max.append(nan_min(a_max, b_max))
        result = MinMax(res_min, res_max)
        return (result if return_flipped or not result.is_flipped else None)
    
    def __contains(self, abs_min, abs_max, point, end_min=True, end_max=True):
        if end_min and end_max:
            return not any((v < v_min) or (v > v_max)
                for v, v_min, v_max in zip(point, abs_min, abs_max) if v is not None)
        elif end_min:
            return not any((v < v_min) or (v >= v_max)
                for v, v_min, v_max in zip(point, abs_min, abs_max) if v is not None)
        elif end_max:
            return not any((v <= v_min) or (v > v_max)
                for v, v_min, v_max in zip(point, abs_min, abs_max) if v is not None)
        else:
            return not any((v <= v_min) or (v >= v_max)
                for v, v_min, v_max in zip(point, abs_min, abs_max) if v is not None)
    
    def intersect(self, other, return_flipped=False):
        abs_min, abs_max = self.abs_range
        
        if isinstance(other, Bounds):
            return self.__intersect(abs_min, abs_max, other, return_flipped)
        else: # iterable is expected
            result = []
            for item in other:
                if not item: continue
                if isinstance(item, Bounds):
                    result.append(self.__intersect(abs_min, abs_max, item, return_flipped))
                elif self.__contains(abs_min, abs_max, item):
                    result.append(item)
            return result
    
    def contains(self, other, end_min=True, end_max=True):
        abs_min, abs_max = self.abs_range
        if isinstance(other, Bounds):
            return (self.__contains(abs_min, abs_max, other.min, end_min, end_max) and
                    self.__contains(abs_min, abs_max, other.max, end_min, end_max))
        else: # point is expected
            return self.__contains(abs_min, abs_max, other, end_min, end_max)
    
    def overlaps(self, other):
        abs_min, abs_max = self.abs_range
        if isinstance(other, Bounds):
            intersection = self.__intersect(abs_min, abs_max, other, True)
            return not intersection.is_flipped
        else: # point is expected
            return self.__contains(abs_min, abs_max, other)
    
    def relative(self, cell):
        cell_min, cell_size = ((cell.min, cell.size) if isinstance(cell, Bounds) else (0.0, cell))
        cell_min = self._convert(cell_min, 0.0, float)
        cell_size = self._convert(cell_size, 1.0, float)
        return Bounds.MinSize((self.min - cell_min) / cell_size, self.size / cell_size)
    
    def point_to_normalized(self, point):
        # numpy divides float arrays with IEEE 754 behavior, so
        # we don't have to worry about zero division exceptions
        return (self._convert(point) - self.min) / self.size
    
    def normalized_to_point(self, point):
        return self.min + self.size * self._convert(point)
    
    def closest_point(self, point):
        return np.clip(self._convert(point), *self.abs_range)
    
    def distance(self, point):
        point = self._convert(point)
        closest = np.clip(point, *self.abs_range)
        return np.linalg.norm(replace_nan(point - closest, 0.0))
    
    def closest_normal(self, point):
        delta = self.point_to_normalized(point) - 0.5
        axis = np.argmax(np.abs(delta))
        result = np.zeros(dimension)
        result[axis] = np.sign(delta[axis])
        return result
    
    # https://medium.com/@bromanz/another-view-on-the-classic-ray-
    #   aabb-intersection-algorithm-for-bvh-traversal-41125138b525
    def intersect_ray(self, origin, direction, tmin=0.0, tmax=1.0):
        origin = self._convert(origin)
        direction = self._convert(direction)
        
        # numpy divides float arrays with IEEE 754 behavior, so
        # we don't have to worry about zero division exceptions
        inv_direction = 1.0 / direction
        
        abs_min, abs_max = self.abs_range
        
        t0 = (abs_min - origin) * inv_direction
        t1 = (abs_max - origin) * inv_direction
        
        if tmin is None: tmin = -math.inf
        if tmax is None: tmax = math.inf
        
        tmin = max(tmin, nan_max(np.minimum(t0, t1), default=tmax))
        tmax = min(tmax, nan_min(np.maximum(t0, t1), default=tmin))
        
        # success = tmin < tmax
        # intersection point = origin + direction * t
        # intersection normal = closest_normal(point)
        return (tmin, tmax)

class MinMax(Bounds):
    __slots__ = ["_len", "_min", "_max"]
    
    def _get(self): return self._min
    def _set(self, value): self._min[:] = self._convert(value)
    min = property(_get, _set)
    
    def _get(self): return self._max
    def _set(self, value): self._max[:] = self._convert(value)
    max = property(_get, _set)
    
    def _get(self): return self._max - self._min
    def _set(self, value):
        delta = (self._convert(value) - self.size) * 0.5
        self._min -= delta
        self._max += delta
    size = property(_get, _set)
    
    def _get(self): return (self._min + self._max) * 0.5
    def _set(self, value):
        delta = self._convert(value) - self.center
        self._min += delta
        self._max += delta
    center = property(_get, _set)
    
    def _get(self): return self._len
    def _set(self, value): self._min, self._max = self._resize(self._min, self._max, value)
    dimension = property(_get, _set)
    
    any_nan = property(lambda self: any(np.isnan(self._min)) or any(np.isnan(self._max)))
    all_nan = property(lambda self: all(np.isnan(self._min)) and all(np.isnan(self._max)))
    
    del _get, _set
    
    def __new__(cls, min=(), max=()):
        self = object.__new__(cls)
        self._min, self._max = self._assign(min, max)
        return self
    
    def __repr__(self):
        return f"Bounds(min = {tuple(self._min)}, max = {tuple(self._max)})"
    
    def __eq__(self, other):
        return np.array_equal(self._min, other.min) and np.array_equal(self._max, other.max)
    
    def __getitem__(self, index):
        if index == 0:
            return self._min
        elif index == 1:
            return self._max
        else:
            raise IndexError(f"Invalid index {index}")
    
    def __setitem__(self, index, value):
        if index == 0:
            self._min[:] = self._convert(value)
        elif index == 1:
            self._max[:] = self._convert(value)
        else:
            raise IndexError(f"Invalid index {index}")
    
    def copy(self, type=None):
        bounds = self._instantiate_type(type or self.type)
        bounds.set_min_max(self._min, self._max)
        return bounds
    
    def set_min_max(self, min, max):
        self._min, self._max = self._assign(min, max)
    
    def set_min_size(self, min, size):
        min, size = self._assign(min, size)
        self._min = (min)[:]
        self._max = (min + size)[:]
    
    def set_max_size(self, max, size):
        max, size = self._assign(max, size)
        self._min = (max - size)[:]
        self._max = (max)[:]
    
    def set_center_size(self, center, size):
        center, extents = self._assign(center, size)
        extents *= 0.5
        self._min = (center - extents)[:]
        self._max = (center + extents)[:]
    
    def _is_flipped(self, index):
        return self._min[index] > self._max[index]
    def _flip(self, index):
        self._min[index], self._max[index] = self._max[index], self._min[index]
    
    def _grow(self, dmin, dmax):
        self._min -= dmin
        self._max += dmax
    
    def _move(self, delta):
        self._min += delta
        self._max += delta
    
    def _scale(self, factor):
        self._min *= factor
        self._max *= factor

Bounds._register(MinMax)

class MinSize(Bounds):
    __slots__ = ["_len", "_min", "_size"]
    
    def _get(self): return self._min
    def _set(self, value): self._min[:] = self._convert(value)
    min = property(_get, _set)
    
    def _get(self): return self._size
    def _set(self, value): self._size[:] = self._convert(value)
    size = property(_get, _set)
    
    def _get(self): return self._min + self._size
    def _set(self, value):
        self._min[:] = self._convert(value) - self._size
    max = property(_get, _set)
    
    def _get(self): return self._min + self._size * 0.5
    def _set(self, value):
        self._min[:] = self._convert(value) - self._size * 0.5
    center = property(_get, _set)
    
    def _get(self): return self._len
    def _set(self, value): self._min, self._size = self._resize(self._min, self._size, value)
    dimension = property(_get, _set)
    
    any_nan = property(lambda self: any(np.isnan(self._min)) or any(np.isnan(self._size)))
    all_nan = property(lambda self: all(np.isnan(self._min)) and all(np.isnan(self._size)))
    
    del _get, _set
    
    def __new__(cls, min=(), size=()):
        self = object.__new__(cls)
        self._min, self._size = self._assign(min, size)
        return self
    
    def __repr__(self):
        return f"Bounds(min = {tuple(self._min)}, size = {tuple(self._size)})"
    
    def __eq__(self, other):
        return np.array_equal(self._min, other.min) and np.array_equal(self._size, other.size)
    
    def __getitem__(self, index):
        if index == 0:
            return self._min
        elif index == 1:
            return self._size
        else:
            raise IndexError(f"Invalid index {index}")
    
    def __setitem__(self, index, value):
        if index == 0:
            self._min[:] = self._convert(value)
        elif index == 1:
            self._size[:] = self._convert(value)
        else:
            raise IndexError(f"Invalid index {index}")
    
    def copy(self, type=None):
        bounds = self._instantiate_type(type or self.type)
        bounds.set_min_size(self._min, self._size)
        return bounds
    
    def set_min_max(self, min, max):
        min, max = self._assign(min, max)
        self._min = (min)[:]
        self._size = (max - min)[:]
    
    def set_min_size(self, min, size):
        self._min, self._size = self._assign(min, size)
    
    def set_max_size(self, max, size):
        max, size = self._assign(max, size)
        self._min = (max - size)[:]
        self._size = (size)[:]
    
    def set_center_size(self, center, size):
        center, size = self._assign(center, size)
        self._min = (center - size * 0.5)[:]
        self._size = (size)[:]
    
    def _is_flipped(self, index):
        return self._size[index] < 0
    def _flip(self, index):
        size = self._size[index]
        self._min[index] += size
        self._size[index] = -size
    
    def _grow(self, dmin, dmax):
        self._min -= dmin
        self._size += dmin + dmax
    
    def _move(self, delta):
        self._min += delta
    
    def _scale(self, factor):
        self._min *= factor
        self._size *= factor

Bounds._register(MinSize)

class MaxSize(Bounds):
    __slots__ = ["_len", "_max", "_size"]
    
    def _get(self): return self._max
    def _set(self, value): self._max[:] = self._convert(value)
    max = property(_get, _set)
    
    def _get(self): return self._size
    def _set(self, value): self._size[:] = self._convert(value)
    size = property(_get, _set)
    
    def _get(self): return self._max - self._size
    def _set(self, value):
        self._max[:] = self._convert(value) + self._size
    min = property(_get, _set)
    
    def _get(self): return self._max - self._size * 0.5
    def _set(self, value):
        self._max[:] = self._convert(value) + self._size * 0.5
    center = property(_get, _set)
    
    def _get(self): return self._len
    def _set(self, value): self._max, self._size = self._resize(self._max, self._size, value)
    dimension = property(_get, _set)
    
    any_nan = property(lambda self: any(np.isnan(self._max)) or any(np.isnan(self._size)))
    all_nan = property(lambda self: all(np.isnan(self._max)) and all(np.isnan(self._size)))
    
    del _get, _set
    
    def __new__(cls, max=(), size=()):
        self = object.__new__(cls)
        self._max, self._size = self._assign(max, size)
        return self
    
    def __repr__(self):
        return f"Bounds(max = {tuple(self._max)}, size = {tuple(self._size)})"
    
    def __eq__(self, other):
        return np.array_equal(self._max, other.max) and np.array_equal(self._size, other.size)
    
    def __getitem__(self, index):
        if index == 0:
            return self._max
        elif index == 1:
            return self._size
        else:
            raise IndexError(f"Invalid index {index}")
    
    def __setitem__(self, index, value):
        if index == 0:
            self._max[:] = self._convert(value)
        elif index == 1:
            self._size[:] = self._convert(value)
        else:
            raise IndexError(f"Invalid index {index}")
    
    def copy(self, type=None):
        bounds = self._instantiate_type(type or self.type)
        bounds.set_max_size(self._max, self._size)
        return bounds
    
    def set_min_max(self, min, max):
        min, max = self._assign(min, max)
        self._max = (max)[:]
        self._size = (max - min)[:]
    
    def set_min_size(self, min, size):
        min, size = self._assign(min, size)
        self._size = (size)[:]
        self._max = (min + size)[:]
    
    def set_max_size(self, max, size):
        self._max, self._size = self._assign(max, size)
    
    def set_center_size(self, center, size):
        center, size = self._assign(center, size)
        self._size = (size)[:]
        self._max = (center + size * 0.5)[:]
    
    def _is_flipped(self, index):
        return self._size[index] < 0
    def _flip(self, index):
        size = self._size[index]
        self._min[index] -= size
        self._size[index] = -size
    
    def _grow(self, dmin, dmax):
        self._max += dmax
        self._size += dmin + dmax
    
    def _move(self, delta):
        self._max += delta
    
    def _scale(self, factor):
        self._max *= factor
        self._size *= factor

Bounds._register(MaxSize)

class CenterSize(Bounds):
    __slots__ = ["_len", "_center", "_size"]
    
    def _get(self): return self._center
    def _set(self, value): self._center[:] = self._convert(value)
    center = property(_get, _set)
    
    def _get(self): return self._size
    def _set(self, value): self._size[:] = self._convert(value)
    size = property(_get, _set)
    
    def _get(self): return self._center - self._size * 0.5
    def _set(self, value):
        self._center[:] = self._convert(value) + self._size * 0.5
    min = property(_get, _set)
    
    def _get(self): return self._center + self._size * 0.5
    def _set(self, value):
        self._center[:] = self._convert(value) - self._size * 0.5
    max = property(_get, _set)
    
    def _get(self): return self._len
    def _set(self, value): self._center, self._size = self._resize(self._center, self._size, value)
    dimension = property(_get, _set)
    
    any_nan = property(lambda self: any(np.isnan(self._center)) or any(np.isnan(self._size)))
    all_nan = property(lambda self: all(np.isnan(self._center)) and all(np.isnan(self._size)))
    
    del _get, _set
    
    def __new__(cls, center=(), size=()):
        self = object.__new__(cls)
        self._center, self._size = self._assign(center, size)
        return self
    
    def __repr__(self):
        return f"Bounds(center = {tuple(self._center)}, size = {tuple(self._size)})"
    
    def __eq__(self, other):
        return np.array_equal(self._center, other.center) and np.array_equal(self._size, other.size)
    
    def __getitem__(self, index):
        if index == 0:
            return self._center
        elif index == 1:
            return self._size
        else:
            raise IndexError(f"Invalid index {index}")
    
    def __setitem__(self, index, value):
        if index == 0:
            self._center[:] = self._convert(value)
        elif index == 1:
            self._size[:] = self._convert(value)
        else:
            raise IndexError(f"Invalid index {index}")
    
    def copy(self, type=None):
        bounds = self._instantiate_type(type or self.type)
        bounds.set_center_size(self._center, self._size)
        return bounds
    
    def set_min_max(self, min, max):
        min, max = self._assign(min, max)
        size = max - min
        self._center = (min + size * 0.5)[:]
        self._size = (max - min)[:]
    
    def set_min_size(self, min, size):
        min, size = self._assign(min, size)
        self._center = (min + size * 0.5)[:]
        self._size = (size)[:]
    
    def set_max_size(self, max, size):
        max, size = self._assign(max, size)
        self._center = (max - size * 0.5)[:]
        self._size = (size)[:]
    
    def set_center_size(self, center, size):
        self._center, self._size = self._assign(center, size)
    
    def _is_flipped(self, index):
        return self._size[index] < 0
    def _flip(self, index):
        self._size[index] = -self._size[index]
    
    def _grow(self, dmin, dmax):
        self._center += (dmax - dmin)*0.5
        self._size += dmin + dmax
    
    def _move(self, delta):
        self._center += delta
    
    def _scale(self, factor):
        self._center *= factor
        self._size *= factor

Bounds._register(CenterSize)
