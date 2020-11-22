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

import numpy as np

import bpy
from mathutils import Vector, Matrix, Quaternion, Euler, Color

from .utils_math import divide, ndrange, nan_min, nan_max, replace_nan
from .bounds import Bounds

#============================================================================#

Number = numbers.Number

_axis_name_map = {
    "x":0, "X":0,
    "y":1, "Y":1,
    "z":2, "Z":2,
}

def _map_axes(axes):
    for axis in axes:
        if not isinstance(axis, str): yield axis
        yield _axis_name_map[axis]

"""
TODO:

grid() -> (start indices, end indices)

// non-grid layouts are all-or-nothing and have O(n) or even worse complexity
// rebuilding on each frame is inefficient, but using a cached version would require keeping track of all dependencies

waterfall: pack items into N bins, trying to keep overall size[axis] as even as possible

justified (2d only):
a) pack into row until width is reached, then begin next row
b) pre-sort by bin packing efficiency (?)

atlas packing?

// note: in blender ui, only grid is possible, since template_icon() can only scale uniformly
"""

class Arranger:
    _box_synonyms = {'BOX', 'CUBE', 'RECT', 'SQUARE'}
    _ball_synonyms = {'BALL', 'SPHERE', 'CIRCLE'}
    
    @classmethod
    def arrange(cls, bboxes, axes=None, scale=1.0, fit_size=None, adjust_size=None):
        """
        axes can be:
        * dict: axis indices/names, and their modes
        * iterable: axis indices/names (mode = BOX)
        * str: axes mode (axis indices = all)
        * None: axis indices = all, mode = BOX
        axis modes: MIN, MAX, CENTER/MID, BOX/CUBE/RECT/SQUARE, BALL/SPHERE/CIRCLE
        """
        
        axes, axis_modes = cls.__get_axes_modes(axes)
        if not axes: return None, None # no work to be done
        
        init_offset = cls.__get_offset_initializer(axes, axis_modes)
        
        scales = []
        offsets = []
        cell_size = np.zeros(1)
        
        for bbox in bboxes:
            center, size = bbox.center, bbox.abs_size
            
            cell_scale = scale * (1.0 if fit_size is None else divide(fit_size, size[axes].max()))
            offset = init_offset(center, size, cell_scale)
            
            scales.append(cell_scale)
            offsets.append(offset)
            
            count = len(size)
            if len(cell_size) < count: cell_size.resize(count)
            cell_size[:count] = np.maximum(cell_size[:count], size * cell_scale)
        
        if not offsets: return offsets, scales
        
        indices_box, indices_ball = cls.__get_shape_indices(axes, axis_modes, cell_size)
        
        if indices_box or indices_ball:
            # E.g.: ensure cell is larger than minimum, add a margin, etc.
            if adjust_size: cell_size = np.array(adjust_size(cell_size))
            
            if indices_box: cls._arrange_box(indices_box, cell_size, offsets)
            if indices_ball: cls._arrange_ball(indices_ball, cell_size, offsets)
        
        return offsets, scales
    
    @classmethod
    def __get_axes_modes(cls, axes):
        if isinstance(axes, dict):
            axes = sorted(axes.items())
            axis_modes = [v for k, v in axes if v]
            axes = list(_map_axes(k for k, v in axes if v))
        elif isinstance(axes, str):
            axis_modes = axes
            axes = Ellipsis
        else:
            axis_modes = 'BOX'
            axes = (Ellipsis if axes is None else sorted(set(_map_axes(axes))))
        
        return axes, axis_modes
    
    @classmethod
    def __get_offset_initializer(cls, axes, axis_modes):
        if isinstance(axis_modes, str):
            is_min = (axis_modes == 'MIN')
            is_max = (axis_modes == 'MAX')
            is_mid = not (is_min or is_max)
            indices_min = (Ellipsis if is_min else None)
            indices_max = (Ellipsis if is_max else None)
            indices_mid = (Ellipsis if is_mid else None)
        else:
            indices_min = []
            indices_max = []
            indices_mid = []
            for axis, axis_mode in zip(axes, axis_modes):
                if axis_mode == 'MIN':
                    indices_min.append(axis)
                elif axis_mode == 'MAX':
                    indices_max.append(axis)
                else: # CENTER, BOX, BALL...
                    indices_mid.append(axis)
        
        use_extents = bool(indices_min or indices_max)
        
        def init_offset(center, size, cell_scale):
            offset = np.zeros(len(size))
            
            # In order for this to work properly, world matrix
            # needs to be scaled and then translated
            scaled_center = (-cell_scale) * center
            if indices_mid: offset[indices_mid] += scaled_center[indices_mid]
            if use_extents:
                scaled_extents = (-cell_scale * 0.5) * size
                if indices_min: offset[indices_min] += scaled_center[indices_min] + scaled_extents[indices_min]
                if indices_max: offset[indices_max] += scaled_center[indices_max] - scaled_extents[indices_max]
            
            return offset
        
        return init_offset
    
    @classmethod
    def __get_shape_indices(cls, axes, axis_modes, cell_size):
        indices_box = []
        indices_ball = []
        
        if isinstance(axis_modes, str):
            if axis_modes in cls._box_synonyms:
                indices_box.extend(range(len(cell_size)))
            elif axis_modes in cls._ball_synonyms:
                indices_ball.extend(range(len(cell_size)))
        else:
            for axis, axis_mode in zip(axes, axis_modes):
                if axis_mode in cls._box_synonyms:
                    indices_box.append(axis)
                elif axis_mode in cls._ball_synonyms:
                    indices_ball.append(axis)
        
        return indices_box, indices_ball
    
    @classmethod
    def _arrange_ball(cls, axes, cell_size, offsets):
        cell_count = len(offsets)
        sizes = cell_size[axes]
        
        counts = cls._optimal_ball_counts(sizes, cell_count)
        
        candidates = []
        for ndindex in ndrange(-counts, counts+1):
            offset = ndindex * sizes
            candidates.append((np.linalg.norm(offset), tuple(offset)))
        candidates.sort() # Note: numpy arrays cannot be sorted (ValueError)
        
        for cell_index in range(cell_count):
            offsets[cell_index][axes] += candidates[cell_index][1]
    
    @classmethod
    def _optimal_ball_counts(cls, sizes, cell_count):
        n = len(sizes) # dimensions
        
        ratios = sizes / sizes.max()
        volume = cell_count * np.prod(ratios)
        
        # https://en.wikipedia.org/wiki/Volume_of_an_n-ball
        coef = math.pi**(n/2) / math.gamma(n/2 + 1)
        radius = (volume / coef) ** (1.0 / n)
        
        counts = [(math.ceil(radius / ratio) + 1) for ratio in ratios]
        return np.array(counts)
    
    @classmethod
    def _arrange_box(cls, axes, cell_size, offsets):
        cell_count = len(offsets)
        sizes = cell_size[axes]
        
        counts = cls._optimal_box_counts(sizes, cell_count)
        
        offset_bbox = -0.5 * sizes * (counts - 1)
        
        cell_index = 0
        for ndindex in ndrange(counts):
            offsets[cell_index][axes] += offset_bbox + ndindex * sizes
            cell_index += 1
            if cell_index >= cell_count: break
    
    @classmethod
    def _optimal_box_counts(cls, sizes, cell_count):
        axes_count = len(sizes)
        if axes_count == 1: return np.array([cell_count])
        
        sizes_indices = sorted(zip(sizes, range(axes_count)))
        sorted_sizes = [size for size, i in sizes_indices]
        indices = [i for size, i in sizes_indices]
        
        calc_cost = (lambda counts: np.linalg.norm(sorted_sizes * counts))
        
        counts = np.ones(axes_count)
        counts[0] = cell_count
        cost = calc_cost(counts)
        
        # Search for optimal box sides via gradient descent
        while True:
            best_counts = counts
            best_cost = cost
            
            for i in range(1, axes_count):
                new_counts = np.array(counts)
                new_counts[i] += 1
                new_counts[0] = math.ceil(cell_count / np.prod(new_counts[1:]))
                new_cost = calc_cost(new_counts)
                
                if new_cost < best_cost:
                    best_cost = new_cost
                    best_counts = new_counts
            
            if best_cost == cost: break
            counts = best_counts
            cost = best_cost
        
        return counts[indices]
    
    #============================================================================#
    
    __grid_index_funcs = {
        'OVERLAP': (lambda v0, v1, dv: (math.floor(v0), math.ceil(v1))),
        'INSIDE': (lambda v0, v1, dv: (math.ceil(v0), math.floor(v1))),
        'SIZE_FLOOR': (lambda v0, v1, dv: (0, math.floor(dv))),
        'SIZE_CEIL': (lambda v0, v1, dv: (0, math.ceil(dv))),
        'SIZE_ROUND': (lambda v0, v1, dv: (0, round(dv))),
    }
    
    @classmethod
    def __grid_indices_parse_modes(cls, modes):
        mode_default = 'OVERLAP'
        
        if modes is None:
            modes = {}
        elif isinstance(modes, dict):
            modes = {_axis_name_map.get(k, k): v for k, v in modes.items()}
        elif isinstance(modes, str):
            mode_default = modes
            modes = {}
        else:
            modes = dict(enumerate(modes))
        
        return modes, mode_default
    
    @classmethod
    def __grid_indices_limit(cls, array, limit, func):
        if isinstance(limit, Number):
            array[:] = [func(v, limit) for v in array]
        elif limit is not None:
            array[:] = [func(v, vm) for v, vm in zip(array, limit)]
    
    @classmethod
    def grid_indices(cls, bbox, cell, modes=None, min_count=None, max_count=None):
        modes, mode_default = cls.__grid_indices_parse_modes(modes)
        
        bbox_rel = bbox.relative(cell)
        rel_min = bbox_rel.min
        rel_max = bbox_rel.max
        rel_size = bbox_rel.size
        
        indices = np.zeros((2, bbox_rel.dimension), int)
        for axis in range(bbox_rel.dimension):
            mode = modes.get(axis, mode_default)
            index_func = cls.__grid_index_funcs.get(mode, None)
            if not index_func: raise ValueError(f"Unrecognized axis mode {mode} at modes[{axis}]")
            indices[:, axis] = index_func(rel_min[axis], rel_max[axis], rel_size[axis])
        
        indices[1] -= indices[0]
        cls.__grid_indices_limit(indices[1], min_count, nan_max)
        cls.__grid_indices_limit(indices[1], max_count, nan_min)
        
        return indices
    
    @classmethod
    def __grid_parse_alignments(cls, alignments, dimension):
        if not alignments: alignments = 'ABS'
        
        if isinstance(alignments, str):
            alignments = [alignments] * dimension
        elif len(alignments) < dimension:
            alignments = list(alignments)
            alignments.extend(['ABS'] * (dimension - len(alignments)))
        
        return alignments
    
    @classmethod
    def __grid_parse_flat(cls, flat):
        if isinstance(flat, Number) and not isinstance(flat, bool):
            return (0, flat) # bool is a Number in python
        
        if hasattr(flat, "__len__"):
            n = len(flat)
            flat_min = (flat[0] if n > 0 else None)
            flat_max = (flat[1] if n > 1 else None)
            if flat_min is None: flat_min = -math.inf
            if flat_max is None: flat_max = math.inf
            return (flat_min, flat_max)
        
        return (-math.inf, math.inf)
    
    @classmethod
    def __grid_parse_sorting(cls, sorting, dimension):
        if (sorting is None) or (len(sorting) == 0):
            return [(i, False) for i in range(dimension)]
        
        result = []
        axes = set()
        for sort_item in sorting:
            invert = False
            if isinstance(sort_item, int):
                axis = sort_item
            elif isinstance(sort_item, str):
                axis = _axis_name_map.get(sort_item, None)
                if not axis: continue
            elif len(sort_item) == 0:
                continue
            else:
                axis = sort_item[0]
                if isinstance(axis, str):
                    axis = _axis_name_map.get(axis, None)
                    if not axis: continue
                invert = (len(sort_item) > 1) and (sort_item[1] < 0)
            
            if (axis < 0) or (axis >= dimension): continue
            
            if axis in axes: continue
            axes.add(axis)
            
            result.append((axis, invert))
        
        return result or [(i, False) for i in range(dimension)]
    
    @classmethod
    def __grid_index_converters(cls, sorting, indices_count):
        dimension = len(indices_count)
        sort_i_max = len(sorting) - 1
        
        stride = 1
        
        index_scale = np.zeros(dimension, int)
        index_offset = 0
        
        nd_mods = np.ones(dimension, int)
        nd_divs = np.ones(dimension, int)
        nd_invs = np.zeros(dimension, bool)
        last_axis = -1
        
        for sort_i, sort_item in enumerate(sorting):
            axis, invert = sort_item
            
            index_scale[axis] = (-1 if invert else 1) * stride
            
            if invert and (sort_i < sort_i_max):
                index_offset += (indices_count[axis] - 1) * stride
            
            nd_mods[axis] = indices_count[axis]
            nd_divs[axis] = stride
            nd_invs[axis] = invert
            last_axis = axis
            
            stride *= indices_count[axis]
        
        nd_mods1 = nd_mods - 1
        
        dot = np.dot
        
        def to_flat(ndindex, use_invert=True):
            ndindex = np.array(ndindex)
            for axis in range(dimension):
                if axis != last_axis:
                    ndindex[axis] = max(min(ndindex[axis], indices_count[axis]-1), 0)
            
            if use_invert: return dot(ndindex, index_scale) + index_offset
            return dot(ndindex, np.abs(index_scale))
        
        def to_ndim(index, use_invert=True):
            ndindex = index // nd_divs
            for axis in range(dimension):
                if axis != last_axis:
                    if use_invert and nd_invs[axis]:
                        ndindex[axis] = nd_mods1[axis] - (ndindex[axis] % nd_mods[axis])
                    else:
                        ndindex[axis] %= nd_mods[axis]
                elif use_invert and nd_invs[axis]:
                    ndindex[axis] = -ndindex[axis]
            return ndindex
        
        return to_flat, to_ndim
    
    @classmethod
    def grid(cls, bbox, cell, indices, alignments=None, flat=None, sorting=None):
        # alignment(s): ABS, MIN/LEFT, MAX/RIGHT, CENTER, JUSTIFY/DISTRIBUTE
        # flat is None: n-dimensional index; otherwise, scalar index (grow along the sorting[-1] axis)
        # flat is not None: True (-inf, inf); int (0, count); tuple (start, count)
        # sorting: [(axis, direction), ...]
        
        if (indices is None) or isinstance(indices, (dict, str)):
            indices = cls.grid_indices(cell, bbox, modes=indices)
        indices_min, indices_count = indices
        
        grid_info = dict(bbox=bbox, indices=indices)
        grid_info.update(alignments=alignments, flat=flat, sorting=sorting)
        
        if flat is None:
            def index_iterator():
                for ndindex in ndrange(indices_count):
                    ndindex += indices_min
                    yield ndindex, ndindex
        else:
            index_min, index_max, to_flat, to_ndim = cls._grid_flat_prepare(flat, sorting, indices)
            grid_info.update(index_min=index_min, index_max=index_max, to_flat=to_flat, to_ndim=to_ndim)
            
            def index_iterator():
                for index in range(index_min, index_max):
                    ndindex = to_ndim(index, True)
                    yield index, ndindex
        
        if isinstance(cell, Bounds):
            cell_min, cell_size = cell.min, cell.size
        else:
            cell_min, cell_size = np.zeros(len(cell)), np.asarray(cell)
        
        pos_min, pos_step = cls._grid_calc_pos(cell_min, cell_size, bbox, indices, alignments)
        
        grid_info.update(cell_min=cell_min, cell_size=cell_size, pos_min=pos_min, pos_step=pos_step)
        
        def ndim_to_bounds(ndindex):
            return Bounds.MinSize(pos_min + ndindex * pos_step, cell_size)
        
        def point_to_ndim(point):
            return np.floor((np.asarray(point) - pos_min) / pos_step).astype(int)
        
        def grid_iterator():
            for index, ndindex in index_iterator():
                yield index, Bounds.MinSize(pos_min + ndindex * pos_step, cell_size)
        
        grid_info.update(ndim_to_bounds=ndim_to_bounds, point_to_ndim=point_to_ndim, iterator=grid_iterator)
        
        return grid_info
    
    @classmethod
    def _grid_flat_prepare(cls, flat, sorting, indices):
        indices_min, indices_count = indices
        dimension = len(indices_count)
        
        flat_min, flat_count = cls.__grid_parse_flat(flat)
        sorting = cls.__grid_parse_sorting(sorting, dimension)
        to_flat, to_ndim = cls.__grid_index_converters(sorting, indices_count)
        
        flat_max = flat_min + flat_count
        index_min = to_flat(indices_min, False)
        index_max = to_flat(indices_min + indices_count - 1, False) + 1
        # Convert to float when comparing so that there won't be
        # "RuntimeWarning: invalid value encountered in greater"
        if flat_min > float(index_min): index_min = int(flat_min)
        if flat_max < float(index_max): index_max = int(flat_max)
        
        if index_min < index_max:
            limits = to_ndim(index_max - 1, False) - to_ndim(index_min, False) + 1
            indices_count = np.array(indices_count) # make a copy, just in case
            
            update_ndim = False
            for axis, invert in reversed(sorting):
                indices_count[axis] = limits[axis]
                if limits[axis] > 1: break
                update_ndim = True
            
            if update_ndim:
                to_flat, to_ndim = cls.__grid_index_converters(sorting, indices_count)
        
        return index_min, index_max, to_flat, to_ndim
    
    @classmethod
    def _grid_calc_pos(cls, cell_min, cell_size, bbox, indices, alignments):
        indices_min, indices_count = indices
        dimension = len(indices_count)
        
        alignments = cls.__grid_parse_alignments(alignments, dimension)
        
        size_diff = bbox.size - cell_size * indices_count
        bbox_min = bbox.min - cell_size * indices_min
        bbox_max = bbox_min + size_diff
        bbox_mid = bbox_min + size_diff * 0.5
        just_size = cell_size + size_diff / indices_count
        just_min = bbox_min + (just_size - cell_size) * 0.5
        
        pos_min, pos_step = np.zeros((2, dimension))
        
        for axis, alignment in enumerate(alignments):
            if alignment == 'ABS':
                pos_min[axis] = cell_min[axis]
                pos_step[axis] = cell_size[axis]
            elif alignment == 'MIN':
                pos_min[axis] = bbox_min[axis]
                pos_step[axis] = cell_size[axis]
            elif alignment == 'MAX':
                pos_min[axis] = bbox_max[axis]
                pos_step[axis] = cell_size[axis]
            elif alignment == 'CENTER':
                pos_min[axis] = bbox_mid[axis]
                pos_step[axis] = cell_size[axis]
            else: # JUSTIFY / DISTRIBUTE
                pos_min[axis] = just_min[axis]
                pos_step[axis] = just_size[axis]
        
        return pos_min, pos_step
    
    @classmethod
    def grid_size(cls, indices, cell, padding=None, flat=None, sorting=None):
        indices_min, indices_count = indices
        dimension = len(indices_count)
        
        if flat is not None:
            flat_min, flat_count = cls.__grid_parse_flat(flat)
            sorting = cls.__grid_parse_sorting(sorting, dimension)
            to_flat, to_ndim = cls.__grid_index_converters(sorting, indices_count)
            
            index_min, index_max = flat_min, flat_min + flat_count
            
            if index_min < index_max:
                limits = to_ndim(index_max - 1, False) - to_ndim(index_min, False) + 1
                indices_count = np.array(indices_count) # make a copy, just in case
                
                for axis, invert in reversed(sorting):
                    indices_count[axis] = limits[axis]
                    if limits[axis] > 1: break
        
        if padding is None:
            padding = np.zeros(dimension)
        elif isinstance(padding, Number):
            padding = np.full(dimension, padding)
        
        cell_size = (cell.size if isinstance(cell, Bounds) else np.asarray(cell))
        
        return cell_size * indices_count + padding * np.maximum(indices_count - 1, 0)
