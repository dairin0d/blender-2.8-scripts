# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import sys
import itertools

import bpy

from mathutils import Vector, Matrix, Quaternion, Euler, Color

from collections import namedtuple

from .utils_python import issubclass_safe
from .utils_math import matrix_flatten, matrix_unflatten
from .utils_text import split_camelcase, compress_whitespace

#============================================================================#

# See also Blender's rna_info module

bpy_struct = bpy.types.AnyType.__base__

class BlRna:
    rna_to_bpy = {
        "BoolProperty":(bpy.props.BoolProperty, bpy.props.BoolVectorProperty),
        "IntProperty":(bpy.props.IntProperty, bpy.props.IntVectorProperty),
        "FloatProperty":(bpy.props.FloatProperty, bpy.props.FloatVectorProperty),
        "StringProperty":bpy.props.StringProperty,
        "EnumProperty":bpy.props.EnumProperty,
        "PointerProperty":bpy.props.PointerProperty,
        "CollectionProperty":bpy.props.CollectionProperty,
    }
    
    def __new__(cls, obj):
        # Note: operator instances have both rna_type and bl_rna,
        # but actual user-defined properties are present only in rna_type
        
        try:
            return obj.rna_type # e.g. operator instances
        except (AttributeError, TypeError, KeyError):
            pass
        
        try:
            return obj.bl_rna # e.g. descendants of bpy_struct
        except AttributeError:
            pass
        
        try:
            return obj.get_rna().bl_rna
        except (AttributeError, TypeError, KeyError):
            pass
        
        try:
            return obj.get_rna_type() # e.g. bpy.ops operators
        except (AttributeError, TypeError, KeyError):
            pass
        
        return None
    
    @staticmethod
    def is_valid(obj):
        if not obj: return False
        try:
            # TypeError: descriptor 'as_pointer' of 'bpy_struct' object needs an argument
            #obj.as_pointer()
            id_data = obj.id_data
            return True
        except ReferenceError:
            return False
    
    @staticmethod
    def parent(obj, n=-1, coerce=True):
        path_parts = obj.path_from_id().split(".")
        if len(path_parts) <= 1: return obj.id_data
        return obj.id_data.path_resolve(".".join(path_parts[:n]), coerce)
    
    @staticmethod
    def full_path(obj):
        if isinstance(obj, bpy.types.ID): return repr(obj.id_data)
        return repr(obj.id_data) + "." + obj.path_from_id()
    
    @staticmethod
    def full_path_resolve(full_path, coerce=True):
        return eval(full_path) # for now
    
    @staticmethod
    def enum_to_int(obj, name, value=None):
        if value is None: value = getattr(obj, name)
        rna_prop = BlRna(obj).properties[name]
        if rna_prop.is_enum_flag:
            res = 0
            for item in rna_prop.enum_items:
                if item.identifier in value: res |= item.value
            return res
        else:
            for item in rna_prop.enum_items:
                if item.identifier == value: return item.value
            return 0
    
    @staticmethod
    def enum_from_int(obj, name, value):
        rna_prop = BlRna(obj).properties[name]
        if rna_prop.is_enum_flag:
            return {item.identifier for item in rna_prop.enum_items if item.value & value}
        else:
            for item in rna_prop.enum_items:
                if item.value == value: return item.identifier
            return None
    
    @staticmethod
    def to_bpy_prop(obj, name=None):
        rna_prop = (obj if name is None else BlRna(obj).properties[name])
        
        type_id = rna_prop.bl_rna.identifier
        bpy_prop = BlRna.rna_to_bpy.get(type_id)
        if not bpy_prop: return None
        
        bpy_args = dict(name=rna_prop.name, description=rna_prop.description, options=set())
        def map_arg(rna_name, bpy_name, is_option=False):
            if is_option:
                if getattr(rna_prop, rna_name, False): bpy_args["options"].add(bpy_name)
            else:
                if hasattr(rna_prop, rna_name): bpy_args[bpy_name] = BlRna.serialize_value(getattr(rna_prop, rna_name), False)
        
        def fix_enum_default():
            # Some built-in properties may have invalid defaults.
            # E.g. ColorManagedInputColorspaceSettings.name has
            # 'NONE' as default, but it's not a recognized value.
            items = bpy_args["items"]
            item_keys = {item[0] for item in items}
            value = bpy_args["default"]
            if isinstance(value, str):
                if value not in item_keys:
                    bpy_args["default"] = items[0][0]
            else:
                bpy_args["default"] = {key for key in value if key in item_keys}
        
        map_arg("is_hidden", 'HIDDEN', True)
        map_arg("is_skip_save", 'SKIP_SAVE', True)
        map_arg("is_animatable", 'ANIMATABLE', True)
        map_arg("is_library_editable", 'LIBRARY_EDITABLE', True)
        if type_id == "EnumProperty":
            map_arg("is_enum_flag", 'ENUM_FLAG', True)
        else:
            pass #map_arg("", 'PROPORTIONAL', True) # no correspondence?
        
        if hasattr(rna_prop, "array_length"):
            if rna_prop.array_length == 0:
                bpy_prop = bpy_prop[0] # bool/int/float
                map_arg("default", "default")
            else:
                bpy_prop = bpy_prop[1] # bool/int/float vector
                map_arg("array_length", "size")
                map_arg("default_array", "default")
            map_arg("subtype", "subtype")
            map_arg("hard_min", "min")
            map_arg("soft_min", "soft_min")
            map_arg("hard_max", "max")
            map_arg("soft_max", "soft_max")
            map_arg("step", "step")
            map_arg("precision", "precision")
        elif type_id == "StringProperty":
            map_arg("default", "default")
            map_arg("subtype", "subtype")
        elif type_id == "EnumProperty":
            map_arg(("default_flag" if rna_prop.is_enum_flag else "default"), "default")
            map_arg("enum_items", "items")
            fix_enum_default()
        else:
            # Caution: fixed_type returns a blender struct, not the original class
            map_arg("fixed_type", "type")
        
        return (bpy_prop, bpy_args)
    
    @staticmethod
    def properties(obj):
        # first rna property item is always rna_type (?)
        return BlRna(obj).properties.items()[1:]
    
    @staticmethod
    def functions(obj):
        return BlRna(obj).functions[funcname].items()
    
    @staticmethod
    def parameters(obj, funcname):
        return BlRna(obj).functions[funcname].parameters.items()
    
    # NOTE: we can't just compare value to the result of get_default(),
    # because in some places Blender's return values not always correspond
    # to the subtype declared in rna (e.g. TRANSFORM_OT_translate.value
    # returns a Vector, but its subtype is 'NONE')
    @staticmethod
    def is_default(value, obj, name=None):
        rna_prop = (obj if name is None else BlRna(obj).properties[name])
        type_id = rna_prop.bl_rna.identifier
        if hasattr(rna_prop, "array_length"):
            if rna_prop.array_length == 0: return value == rna_prop.default
            if isinstance(value, Matrix): value = itertools.chain(*value.col)
            return tuple(rna_prop.default_array) == tuple(value)
        elif type_id == "StringProperty":
            return value == rna_prop.default
        elif type_id == "EnumProperty":
            if rna_prop.is_enum_flag: return set(value) == rna_prop.default_flag
            return value == rna_prop.default
        return False
    
    @staticmethod
    def get_default(obj, name=None):
        rna_prop = (obj if name is None else BlRna(obj).properties[name])
        type_id = rna_prop.bl_rna.identifier
        if hasattr(rna_prop, "array_length"):
            if rna_prop.array_length == 0: return rna_prop.default
            type_id = rna_prop.bl_rna.identifier
            if type_id != "FloatProperty": return tuple(rna_prop.default_array)
            return BlRna._convert_float_array(rna_prop)
        elif type_id == "StringProperty":
            return rna_prop.default
        elif type_id == "EnumProperty":
            return (rna_prop.default_flag if rna_prop.is_enum_flag else rna_prop.default)
        return None
    
    def _convert_float_array():
        def convert_color(rna_prop):
            return Color(rna_prop.default_array)
        def convert_vector(rna_prop):
            return Vector(rna_prop.default_array)
        def convert_matrix(rna_prop):
            #size = (3 if rna_prop.array_length == 9 else 4)
            #arr_iter = iter(rna_prop.default_array)
            #return Matrix(tuple(tuple(itertools.islice(arr_iter, size)) for i in range(size)))
            return matrix_unflatten(rna_prop.default_array)
        def convert_euler_quaternion(rna_prop):
            return (Euler if rna_prop.array_length == 3 else Quaternion)(rna_prop.default_array)
        
        color = ((3,), convert_color)
        vector = ((2,3,4), convert_vector)
        matrix = ((9,16), convert_matrix)
        euler_quaternion = ((3,4), convert_euler_quaternion)
        
        math_types = {
            'COLOR':color, 'COLOR_GAMMA':color,
            'TRANSLATION':vector, 'DIRECTION':vector, 'VELOCITY':vector, 'ACCELERATION':vector, 'XYZ':vector,
            'MATRIX':matrix,
            'EULER':euler_quaternion, 'QUATERNION':euler_quaternion,
        }
        
        @staticmethod
        def convert(rna_prop):
            math_type = math_types.get(rna_prop.subtype)
            if (math_type is None) or (rna_prop.array_length not in math_type[0]):
                return tuple(rna_prop.default_array)
            else:
                return math_type[1](rna_prop)
        
        return convert
    
    _convert_float_array = _convert_float_array()
    
    @staticmethod
    def reset(obj, ignore_default=False):
        if hasattr(obj, "property_unset"): # method of bpy_struct
            for name, rna_prop in BlRna.properties(obj):
                obj.property_unset(name)
        else:
            for name, rna_prop in BlRna.properties(obj):
                if (not ignore_default) or (not BlRna.is_default(getattr(obj, name), rna_prop)):
                    setattr(obj, name, BlRna.get_default(rna_prop))
    
    @staticmethod
    def serialize_value(value, recursive=True, json=False, structs=False, is_struct=False):
        """Serialize rna property value"""
        if isinstance(value, bpy.types.ID):
            # We have to store ID block type along with its name,
            # since some properties don't reference specific
            # ID types (e.g. Object.data's type is ID)
            value = (type(value).__name__, value.name_full)
        else:
            value_class = value.__class__
            base_class = value_class.__base__
            class_name = value_class.__name__
            
            if (base_class is bpy.types.PropertyGroup) or (structs and is_struct):
                if recursive:
                    rna_names = ("bl_rna", "rna_type")
                    value = {
                        rna_prop.identifier:BlRna.serialize_value(getattr(value, rna_prop.identifier),
                            recursive=recursive, structs=structs, is_struct=rna_prop.is_never_none)
                        for name, rna_prop in BlRna(value).properties.items() if name not in rna_names
                    }
            elif value_class is bpy.types.EnumPropertyItem:
                value = (value.identifier, value.name, value.description, value.icon, value.value)
            elif class_name == "bpy_prop_array":
                value = tuple(value) # bool/int/float vector
            elif class_name == "bpy_prop_collection":
                value = [BlRna.serialize_value(item, recursive) for item in value]
            elif class_name == "bpy_prop_collection_idprop":
                value = [BlRna.serialize_value(item, recursive) for item in value]
        
        if json:
            if isinstance(value, set):
                value = list(value) # json.dumps() does not accept sets
        
        return value
    
    @staticmethod
    def serialize(obj, ignore_default=False, json=False, structs=False):
        """Serialize object's rna properties"""
        if not obj: return None
        data = {}
        for name, rna_prop in BlRna.properties(obj):
            if ignore_default and (not obj.is_property_set(rna_prop.identifier)): continue
            value = getattr(obj, rna_prop.identifier)
            data[rna_prop.identifier] = BlRna.serialize_value(value, json=json,
                structs=structs, is_struct=rna_prop.is_never_none)
        return data
    
    @staticmethod
    def deserialize(obj, data, ignore_default=False, suppress_errors=False):
        """Deserialize object's rna properties"""
        if (not obj) or (not data): return
        
        if not isinstance(data, dict): data = BlRna.serialize(data)
        
        rna_props = BlRna(obj).properties
        for name, value in data.items():
            rna_prop = rna_props.get(name)
            if rna_prop is None: continue
            
            type_id = rna_prop.bl_rna.identifier
            
            if type_id == "PointerProperty":
                if isinstance(value, bpy_struct) and not (rna_prop.is_readonly or rna_prop.is_never_none):
                    try:
                        setattr(obj, name, value)
                    except:
                        # sometimes Blender's rna is incomplete/incorrect
                        if not suppress_errors: raise
                elif isinstance(rna_prop.fixed_type, bpy.types.ID):
                    if rna_prop.is_readonly: continue
                    
                    if value is not None:
                        bpy_data = BpyData.to_data(value[0])
                        value = bpy_data.get(value[1])
                    
                    try:
                        setattr(obj, name, value)
                    except:
                        # sometimes Blender's rna is incomplete/incorrect
                        if not suppress_errors: raise
                else:
                    BlRna.deserialize(getattr(obj, name), value, ignore_default, suppress_errors)
            elif type_id == "CollectionProperty":
                collection = getattr(obj, name)
                # collection.add() generally only works for bpy.props.CollectionProperty
                if collection.__class__.__name__ != "bpy_prop_collection_idprop": continue
                
                collection.clear()
                if isinstance(rna_prop.fixed_type, bpy.types.ID):
                    # Blender does not yet support defining ID collection properties; this is just in case
                    for item in value:
                        collection.add()
                        if item is not None:
                            bpy_data = BpyData.to_data(item[0])
                            collection[-1] = bpy_data.get(item[1])
                else:
                    for item in value:
                        BlRna.deserialize(collection.add(), item, ignore_default, suppress_errors)
            else:
                if rna_prop.is_readonly: continue
                
                if (not ignore_default) or (not BlRna.is_default(value, rna_prop)):
                    if (type_id == "EnumProperty") and rna_prop.is_enum_flag and (not isinstance(value, set)):
                        value = set(value) # might be other collection type when loaded from JSON
                    
                    try:
                        setattr(obj, name, value)
                    except:
                        # sometimes Blender's rna is incomplete/incorrect
                        if not suppress_errors: raise
    
    @staticmethod
    def compare_prop(rna_prop, valueA, valueB):
        if rna_prop.type == 'POINTER':
            ft = rna_prop.fixed_type
            if isinstance(ft, bpy.types.ID):
                # idblocks are used only by reference
                return valueA == valueB
            return BlRna.compare(valueA, valueB)
        elif rna_prop.type == 'COLLECTION':
            if len(valueA) != len(valueB): return False
            return all(BlRna.compare(valueA[i], valueB[i]) for i in range(len(valueA)))
        else: # primitive types or enum
            if hasattr(rna_prop, "array_length"):
                if rna_prop.array_length != 0:
                    if not isinstance(valueA, Matrix):
                        return tuple(valueA) == tuple(valueB)
            return valueA == valueB
    
    @staticmethod
    def compare(objA, objB, ignore=(), specials={}):
        """Compare objects' rna properties"""
        if (objA is None) and (objB is None): return True
        if (objA is None) or (objB is None): return False
        if objA == objB: return True
        # objects are expected to be of the same type
        for name, rna_prop in BlRna.properties(objA):
            if name in ignore: continue
            valueA = getattr(objA, name)
            valueB = getattr(objB, name)
            if name in specials:
                if not specials[name](rna_prop, valueA, valueB): return False
            elif not BlRna.compare_prop(rna_prop, valueA, valueB):
                #print("Not same: {} in {}/{}".format(name, type(valueA), type(valueB)))
                return False
        return True

class BpyData:
    bpy_type_to_data = {}
    bpy_data_to_type = {}
    
    @classmethod
    def match_bpy_ID_type_and_data(cls):
        ID_types = []
        for name in dir(bpy.types):
            if name == "ID": continue
            t = getattr(bpy.types, name)
            if not issubclass(t, bpy.types.ID): continue
            ID_types.append(name)
        
        ID_datas = []
        for name in dir(bpy.data):
            if name in ("bl_rna", "rna_type"): continue
            d = getattr(bpy.data, name)
            if not hasattr(d, "foreach_get"): continue
            ID_datas.append(name)
        
        cls.bpy_type_to_data = {}
        cls.bpy_data_to_type = {}
        for data_name in ID_datas:
            data_name = data_name.lower()
            best_matches = (0, 0.0)
            best_type_name = None
            for type_name in ID_types:
                abs_matches = 0
                parts_count = 0
                for name_part in split_camelcase(type_name):
                    name_part = name_part.lower()
                    name_part = name_part[:-1] # for e.g. Library -> libraries
                    part_len = len(name_part)
                    if name_part in data_name: abs_matches += part_len
                    parts_count += part_len
                rel_matches = abs_matches / float(parts_count)
                matches = (abs_matches, rel_matches)
                if matches > best_matches:
                    best_matches = matches
                    best_type_name = type_name
            if not best_type_name: continue
            cls.bpy_type_to_data[best_type_name] = data_name
            cls.bpy_data_to_type[data_name] = best_type_name
    
    @classmethod
    def to_data(cls, bpy_type):
        # Since some version of Blender 2.8 beta, bpy.data is empty
        # (not populated) during addon loading (maybe Blender bug?)
        if not cls.bpy_type_to_data: cls.match_bpy_ID_type_and_data()
        if not isinstance(bpy_type, str): bpy_type = bpy_type.bl_rna.identifier
        return getattr(bpy.data, cls.bpy_type_to_data[bpy_type])

class BpyOp:
    @staticmethod
    def convert(idname, py):
        op_parts = (idname.split(".") if "." in idname else idname.rsplit("_OT_", 1))
        if py: return f"{op_parts[-2].lower()}.{op_parts[-1].lower()}"
        return f"{op_parts[-2].upper()}_OT_{op_parts[-1].lower()}"
    
    def __new__(cls, op):
        if isinstance(op, BpyOp): return op # just in case
        
        try:
            if isinstance(op, str):
                op_parts = (op.split(".") if "." in op else op.rsplit("_OT_", 1))
                category = getattr(bpy.ops, op_parts[-2].lower())
                op = getattr(category, op_parts[-1].lower())
            rna = op.get_rna_type()
            if not rna: return None
        except (AttributeError, KeyError, IndexError):
            return None
        
        self = object.__new__(cls)
        self.op = op
        self.rna = rna
        return self
    
    def __call__(self, *args, **kwargs):
        self.op(*args, **kwargs)
    
    # Emulate operator's public API
    get_rna_type = property(lambda self: self.op.get_rna_type)
    idname = property(lambda self: self.op.idname)
    idname_py = property(lambda self: self.op.idname_py)
    poll = property(lambda self: self.op.poll)
    
    type = property(lambda self: bpy.types.Operator.bl_rna_get_subclass_py(self.op.idname()))

class BpyProp:
    """Utility class for easy inspection/modification of bpy properties"""
    
    base_type = type(bpy.props.BoolProperty) # builtin_function_or_method
    
    # I have no idea how to get the default values using reflection
    # (it seems like bpy.props.* functions have no python-accessible signature)
    known = {
        bpy.props.BoolProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, get=None, set=None,
            default=False, subtype='NONE',
        ),
        bpy.props.BoolVectorProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, get=None, set=None,
            default=(False, False, False), subtype='NONE',
            size=3,
        ),
        bpy.props.IntProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, get=None, set=None,
            default=0, subtype='NONE',
            min=-2**31, soft_min=-2**31,
            max=2**31-1, soft_max=2**31-1,
            step=1,
        ),
        bpy.props.IntVectorProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, get=None, set=None,
            default=(0, 0, 0), subtype='NONE',
            min=-2**31, soft_min=-2**31,
            max=2**31-1, soft_max=2**31-1,
            step=1, size=3,
        ),
        bpy.props.FloatProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, get=None, set=None,
            default=0.0, subtype='NONE', unit='NONE',
            min=sys.float_info.min, soft_min=sys.float_info.min,
            max=sys.float_info.max, soft_max=sys.float_info.max,
            step=3, precision=2,
        ),
        bpy.props.FloatVectorProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, get=None, set=None,
            default=(0.0, 0.0, 0.0), subtype='NONE', unit='NONE',
            min=sys.float_info.min, soft_min=sys.float_info.min,
            max=sys.float_info.max, soft_max=sys.float_info.max,
            step=3, precision=2, size=3,
        ),
        bpy.props.StringProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, get=None, set=None,
            default="", subtype='NONE', maxlen=0,
        ),
        bpy.props.EnumProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, get=None, set=None,
            default="", items=None,
        ),
        bpy.props.PointerProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(), update=None, poll=None,
            type=None,
        ),
        bpy.props.CollectionProperty:dict(
            name="", description="", options={'ANIMATABLE'},
            tags=set(),
            type=None,
        ),
    }
    
    vectors = (bpy.props.BoolVectorProperty,
               bpy.props.IntVectorProperty,
               bpy.props.FloatVectorProperty)
    
    @staticmethod
    def validate(value):
        """Test whether a given object is a bpy property"""
        return (isinstance(value, tuple) and (len(value) == 2) and
                isinstance(value[0], BpyProp.base_type) and # make sure it's a hashable type
                (value[0] in BpyProp.known) and isinstance(value[1], dict))
    
    @staticmethod
    def iterate(cls, only_type=False, exclude_hidden=False, names=None, static=True, dynamic=True, inherited=False):
        """Iterate over bpy properties in a class"""
        if not isinstance(cls, type): cls = type(cls)
        if isinstance(names, str): names = (names,)
        
        # Note: python classes seem to always have __dict__
        # (a mappingproxy object); __slots__ affect only instances
        def _iterate(cls, storage):
            storage = getattr(cls, storage, None)
            if not storage: return
            for name in (storage.keys() if names is None else names):
                if name.startswith("_"): continue # bpy prop name cannot start with an underscore
                value = storage.get(name)
                if not BpyProp.validate(value): continue
                if exclude_hidden and ('HIDDEN' in value[1].get("options", "")): continue
                yield (name, (value[0] if only_type else BpyProp(value, True)))
        
        # It seems that in some cases Blender ignores annotations/attributes in parent classes?
        
        if inherited:
            def _iterate_inherited(cls):
                for base in cls.__bases__:
                    if base is object: continue
                    yield from _iterate_inherited(base)
                    if static: yield from _iterate(base, "__annotations__")
                    if dynamic: yield from _iterate(base, "__dict__")
            yield from _iterate_inherited(cls)
        
        if static: yield from _iterate(cls, "__annotations__")
        if dynamic: yield from _iterate(cls, "__dict__")
    
    @staticmethod
    def is_in(cls, exclude_hidden=False):
        """Test whether a given class contains any bpy properties"""
        return any(BpyProp.iterate(cls, True, exclude_hidden))
    
    @staticmethod
    def reset(obj, names=None, recursive=False):
        """Reset properties of an instance to their default values"""
        for key, info in BpyProp.iterate(type(obj), names=names):
            if info.type == bpy.props.PointerProperty:
                if recursive:
                    _obj = getattr(obj, key)
                    BpyProp.reset(_obj, None, True)
            elif info.type == bpy.props.CollectionProperty:
                if recursive:
                    for _obj in getattr(obj, key):
                        BpyProp.reset(_obj, None, True)
            else:
                if hasattr(obj, "property_unset"):
                    obj.property_unset(key)
                else:
                    setattr(obj, key, info["default"])
    
    @staticmethod
    def deserialize(obj, data, cls=None, names=None, use_skip_save=False):
        """Deserialize object properties from a JSON data structure"""
        
        if not cls: cls = type(obj)
        
        for key, info in BpyProp.iterate(cls, names=names):
            if use_skip_save and ('SKIP_SAVE' in info["options"]): continue
            
            try:
                data_value = data[key]
            except TypeError:
                return # data is not a dictionary
            except KeyError:
                continue
            
            if info.type == bpy.props.PointerProperty:
                _cls = info["type"]
                _obj = getattr(obj, key)
                BpyProp.deserialize(_obj, data_value, _cls)
            elif info.type == bpy.props.CollectionProperty:
                if not isinstance(data_value, list): continue
                _cls = info["type"]
                collection = getattr(obj, key)
                while len(collection) != 0:
                    collection.remove(0)
                for _data in data_value:
                    _obj = collection.add()
                    BpyProp.deserialize(_obj, _data, _cls)
            else:
                try:
                    setattr(obj, key, data_value)
                except (TypeError, ValueError):
                    pass # wrong data
    
    @staticmethod
    def serialize(obj, cls=None, names=None, use_skip_save=False):
        """Serialize object properties to a JSON data structure"""
        
        if not cls: cls = type(obj)
        
        data = {}
        
        for key, info in BpyProp.iterate(cls, names=names):
            if use_skip_save and ('SKIP_SAVE' in info["options"]): continue
            
            data_value = getattr(obj, key)
            
            if info.type == bpy.props.PointerProperty:
                _cls = info["type"]
                _obj = data_value
                data[key] = BpyProp.serialize(_obj, _cls)
            elif info.type == bpy.props.CollectionProperty:
                _cls = info["type"]
                data[key] = [BpyProp.serialize(_obj, _cls) for _obj in data_value]
            elif info.type in BpyProp.vectors:
                if isinstance(data_value, Matrix): data_value = matrix_flatten(data_value)
                data[key] = list(data_value)
            else:
                data[key] = data_value
        
        return data
    
    @staticmethod
    def __dummy_update(self, context):
        """Used for cases when there is no actual update logic, but changes to the property should still redraw the UI"""
        pass
    
    # ======================================================================= #
    
    def __new__(cls, arg0, arg1=None):
        self = None
        if arg1 is True: # arg0 is a validated bpy prop
            self = object.__new__(cls)
            self.type, self.args = arg0
        elif arg1 is None: # arg0 is potentially a bpy prop
            if cls.validate(arg0):
                self = object.__new__(cls)
                self.type, self.args = arg0
        elif isinstance(arg1, str): # arg0 is object/type, arg1 is property name
            if not isinstance(arg0, type): arg0 = type(arg0)
            annotations = getattr(arg0, "__annotations__", None)
            arg0_ann = (annotations.get(arg1) if annotations else None)
            if cls.validate(arg0_ann):
                self = object.__new__(cls)
                self.type, self.args = arg0_ann
            else:
                arg0 = getattr(arg0, arg1, None)
                if cls.validate(arg0):
                    self = object.__new__(cls)
                    self.type, self.args = arg0
        return self
    
    def __call__(self, copy=False):
        """Create an equivalent bpy.props.* structure"""
        return (self.type, self.args.copy() if copy else self.args)
    
    # Extra attributes are useful to store custom callbacks and various metadata
    # (accessible via type(obj)'s attributes / __annotations__).
    # Right now, Blender apparently doesn't check the exact type of options/tags
    # (though it seems that tags aren't available for abritrary classes).
    
    class __set_extra(set):
        pass
    
    @property
    def extra(self):
        options = self.args.get("options", None)
        return getattr(options, "extra", None)
    @extra.setter
    def extra(self, value):
        options = self.args.get("options", None)
        extra = getattr(options, "extra", None)
        if extra is value: return
        options_extra = options
        if not hasattr(options_extra, "extra"):
            options_extra = self.__set_extra()
            if options is None: options = {'ANIMATABLE'}
            if options: options_extra.update(options)
            self.args["options"] = options_extra
        options_extra.extra = value
    
    def get(self, name, default=None):
        return self.args.get(name, default)
    
    def __getitem__(self, name):
        return self.args[name]
    
    def __setitem__(self, name, value):
        if name == "options":
            extra = self.extra
            if extra is not None:
                value = self.__set_extra(value)
                value.extra = extra
        self.args[name] = value
    
    def __contains__(self, name):
        return name in self.args
    
    def __len__(self):
        return len(self.args)
    
    def __iter__(self):
        return self.args.keys()
    
    def items(self):
        return self.args.items()
    
    def keys(self):
        return self.args.keys()
    
    def values(self):
        return self.args.values()
    
    def update(self, d, replace_old=True):
        for k, v in d.items():
            if replace_old or (k not in self.args):
                self[k] = v

# ===== PRIMITIVE ITEMS & PROP ===== #

PrimitiveItem = [
    ("Bool", "Bool", dict()),
    ("Int", "Int", dict()),
    ("Float", "Float", dict()),
    ("String", "String", dict()),
    ("Color", "FloatVector", dict(subtype='COLOR', size=3, min=0.0, max=1.0)),
    ("Euler", "FloatVector", dict(subtype='EULER', size=3)),
    ("Quaternion", "FloatVector", dict(subtype='QUATERNION', size=4)),
    ("Matrix3", "FloatVector", dict(subtype='MATRIX', size=9)),
    ("Matrix4", "FloatVector", dict(subtype='MATRIX', size=16)),
    ("Vector2", "FloatVector", dict(subtype='XYZ', size=2)),
    ("Vector3", "FloatVector", dict(subtype='XYZ', size=3)),
    ("Vector4", "FloatVector", dict(subtype='XYZ', size=4)),
]
PrimitiveItem = type("PrimitiveItem", (object,), {
    n:type(n, (bpy.types.PropertyGroup,), {"__annotations__":{"value":getattr(bpy.props, p+"Property")(**d)}})
    for n, p, d in PrimitiveItem
})
# Seems like class registration MUST be done in global namespace
for item_name in dir(PrimitiveItem):
    if not item_name.startswith("_"):
        bpy.utils.register_class(getattr(PrimitiveItem, item_name))
del item_name

def enum_memorizer(func):
    if hasattr(func, "is_enum_memorizer"): return func # already wrapped
    
    # Blender requires EXACTLY THE SAME string objects,
    # otherwise there will be glitches (and possibly crashes?)
    strings = {}
    def checker(item):
        for v in item:
            if isinstance(v, str):
                s = strings.get(v)
                if s is None:
                    s = str(v) # copy!
                    strings[s] = s
                v = s
            yield v
    
    items = []
    def wrapper(self, context):
        items.clear()
        items.extend(tuple(checker(item)) for item in func(self, context))
        return items
    
    wrapper.is_enum_memorizer = True
    wrapper.items = items
    
    return wrapper

class prop:
    """
    A syntactic sugar for more concise bpy properties declaration.
    By abusing operator overloading, we can declare them
    in a way more similar to conventional attributes. Examples:
    
    pointer_property = SomePropertyGroup | prop()
    
    enum_property = 'A' | prop(items=['A', 'B', 'C'])
    enum_property = {'A', 'B'} | prop(items=['A', 'B', 'C'])
    enum_property = {} | prop(items=['A', 'B', 'C'])
    
    bool_property = True | prop()
    int_property = 5 | prop()
    float_property = 3.14 | prop()
    string_property = "Suzanne" | prop()
    
    bool_vector_property = (True, False) | prop()
    int_vector_property = (5, 7, -3, 0) | prop()
    float_vector_property = (3.14, -0.8, 2.7e-3) | prop()
    float_vector_property = <mathutils instance (Vector, Matrix, etc.)> | prop()
    
    collection_property = [SomePropertyGroup] | prop()
    collection_property = [<primitive value or mathutils instance>] | prop() # will use a predefined property group
    collection_property = [<primitive or mathutils type>] | prop() # will use a predefined property group
    """
    
    def __init__(self, name=None, description=None, **kwargs):
        if name is not None: kwargs["name"] = compress_whitespace(name)
        if description is not None: kwargs["description"] = compress_whitespace(description)
        self.kwargs = kwargs
        self.options = set(self.kwargs.get("options", ('ANIMATABLE',)))
        self.kwargs["options"] = self.options
    
    def __pos__(self): # +prop()
        self.options.add('LIBRARY_EDITABLE')
        return self
    
    def __neg__(self): # -prop()
        self.options.add('HIDDEN')
        self.options.add('SKIP_SAVE')
        self.options.discard('ANIMATABLE')
        return self
    
    def __invert__(self): # ~prop()
        self.options.add('PROPORTIONAL')
        return self
    
    def make(self, value):
        self.options.update(self.kwargs.get("options", ()))
        return self.parse_arguments(value, self.kwargs)
    
    # "|" is the overloadable binary operator
    # with the lowest precedence that can
    # still return non-boolean values
    __ror__ = make
    __rlshift__ = make
    __rrshift__ = make
    
    # ========= ARGUMENT PARSING ========= #
    types_primitive = {
        bool:bpy.props.BoolProperty,
        int:bpy.props.IntProperty,
        float:bpy.props.FloatProperty,
        str:bpy.props.StringProperty,
    }
    
    types_primitive_vector = {
        bool:bpy.props.BoolVectorProperty,
        int:bpy.props.IntVectorProperty,
        float:bpy.props.FloatVectorProperty,
    }
    
    types_float_vector_subtype = {
        Vector:'XYZ',
        Matrix:'MATRIX',
        Euler:'EULER',
        Quaternion:'QUATERNION',
        Color:'COLOR',
    }
    
    types_item_instance = {
        bool:PrimitiveItem.Bool,
        int:PrimitiveItem.Int,
        float:PrimitiveItem.Float,
        str:PrimitiveItem.String,
        Color:PrimitiveItem.Color,
        Euler:PrimitiveItem.Euler,
        Quaternion:PrimitiveItem.Quaternion,
        Matrix:(None, None, PrimitiveItem.Matrix3, PrimitiveItem.Matrix4),
        Vector:(None, PrimitiveItem.Vector2, PrimitiveItem.Vector3, PrimitiveItem.Vector4),
    }
    
    types_item_class = {
        bool:PrimitiveItem.Bool,
        int:PrimitiveItem.Int,
        float:PrimitiveItem.Float,
        str:PrimitiveItem.String,
        Color:PrimitiveItem.Color,
        Euler:PrimitiveItem.Euler,
        Quaternion:PrimitiveItem.Quaternion,
        Matrix:PrimitiveItem.Matrix4,
        Matrix.to_3x3:PrimitiveItem.Matrix3,
        Matrix.to_4x4:PrimitiveItem.Matrix4,
        Vector:PrimitiveItem.Vector3,
        Vector.to_2d:PrimitiveItem.Vector2,
        Vector.to_3d:PrimitiveItem.Vector3,
        Vector.to_4d:PrimitiveItem.Vector4,
    }
    
    def parse_arguments(self, value, kwargs):
        if value is None: value = kwargs.get("default")
        
        value_target = "default"
        vtype = type(value)
        
        err_msg = "Unexpected property value {}; impossible to infer property type".format(repr(value))
        
        ptr_types = (bpy.types.PropertyGroup, bpy.types.ID)
        
        if BpyProp.validate(value):
            value_target = None
        elif issubclass_safe(value, ptr_types): # a = SomePG | prop()
            bpy_type = bpy.props.PointerProperty
            value_target = "type"
        elif ("items" in kwargs) and isinstance(value, str): # a = 'A' | prop(items=['A', 'B', 'C'])
            bpy_type = bpy.props.EnumProperty
            value = self.complete_enum_items(kwargs, value)
        elif vtype in self.types_primitive: # a = 1024.0 | prop()
            bpy_type = self.types_primitive[vtype]
        elif vtype in self.types_float_vector_subtype: # a = Vector() | prop()
            bpy_type = bpy.props.FloatVectorProperty
            if vtype is Matrix: value = matrix_flatten(value)
            if "subtype" not in kwargs: kwargs["subtype"] = self.types_float_vector_subtype[vtype]
            if kwargs["subtype"] == 'COLOR': # need to set min-max to 0..1, otherwise glitches
                kwargs.setdefault("min", 0.0)
                kwargs.setdefault("max", 1.0)
                if "alpha" in kwargs: value = (value[0], value[1], value[2], kwargs.pop("alpha"))
            value = tuple(value)
            kwargs["size"] = len(value)
        elif isinstance(value, tuple) and value: # a = (False, False, False) | prop()
            itype = type(value[0])
            if itype in self.types_primitive_vector:
                bpy_type = self.types_primitive_vector[itype]
                if kwargs.get("subtype") == 'COLOR': # need to set min-max to 0..1, otherwise glitches
                    kwargs.setdefault("min", 0.0)
                    kwargs.setdefault("max", 1.0)
                kwargs["size"] = len(value)
            else:
                raise TypeError(err_msg)
        elif isinstance(value, list) and value: # a = [...] | prop()
            bpy_type = bpy.props.CollectionProperty
            value_target = "type"
            item = value[0]
            itype = type(item)
            if isinstance(item, dict): # a = [dict(prop1=..., prop2=..., ...)] | prop()
                value = type(kwargs.get("name", "<Auto PropertyGroup>"),
                    (bpy.types.PropertyGroup,), {"__annotations__":item})
                value.__name__ += ":AUTOREGISTER" # for AddonManager
            elif itype in self.types_item_instance: # a = [Matrix()] | prop()
                value = self.types_item_instance[itype]
                if not isinstance(value, type): value = value[len(item)]
            elif issubclass_safe(item, ptr_types): # a = [SomePG] | prop()
                value = item
            elif item in self.types_item_class: # a = [Matrix] | prop()
                value = self.types_item_class[item]
            else:
                raise TypeError(err_msg)
        elif isinstance(value, set): # a = {'A', 'B'} | prop(items=['A', 'B', 'C'])
            bpy_type = bpy.props.EnumProperty
            value = self.complete_enum_items(kwargs, value, True)
        elif isinstance(value, dict): # a = {...} | prop()
            if "items" not in kwargs: # a = dict(prop1=..., prop2=..., ...) | prop()
                bpy_type = bpy.props.PointerProperty
                value_target = "type"
                value = type(kwargs.get("name", "<Auto PropertyGroup>"),
                    (bpy.types.PropertyGroup,), {"__annotations__":value})
                value.__name__ += ":AUTOREGISTER" # for AddonManager
            elif not value: # a = {} | prop(items=['A', 'B', 'C'])
                bpy_type = bpy.props.EnumProperty
                value = self.complete_enum_items(kwargs, value, True)
            else:
                raise TypeError(err_msg)
        else:
            raise TypeError(err_msg)
        
        if value_target is not None:
            if value is None:
                kwargs.pop(value_target, None)
            else:
                kwargs[value_target] = value
            prop_info = BpyProp((bpy_type, {}), True)
        else:
            prop_info = BpyProp(value, True)
        
        if "extra" in kwargs:
            prop_info.extra = kwargs["extra"]
            del kwargs["extra"]
        
        prop_info.update(kwargs)
        
        if prop_info.type == bpy.props.EnumProperty:
            items = prop_info.get("items")
            if callable(items): prop_info["items"] = enum_memorizer(items)
        
        return prop_info()
    
    @classmethod
    def get_enum_icon_number(cls, v, v_len, is_icon, default=None):
        if v_len <= 3: return default
        if isinstance(v[3], str) == is_icon: return v[3]
        if v_len <= 4: return default
        if isinstance(v[4], str) == is_icon: return v[4]
        return default
    
    @classmethod
    def expand_enum_item(cls, v, id):
        if isinstance(v, str): return (v, v, "", 'NONE', id)
        
        key = v[0]
        v_len = len(v)
        label = compress_whitespace(v[1] if v_len > 1 else key)
        tip = compress_whitespace(v[2] if v_len > 2 else "")
        icon = cls.get_enum_icon_number(v, v_len, True, 'NONE')
        number = cls.get_enum_icon_number(v, v_len, False, id)
        
        return (key, label, tip, icon, number)
    
    @classmethod
    def complete_enum_items(cls, kwargs, value, enum_flag=False):
        options = kwargs.get("options")
        if options and ('ENUM_FLAG' in options):
            enum_flag = True
        elif enum_flag:
            kwargs.setdefault("options", set()).add('ENUM_FLAG')
        
        items = kwargs.get("items", ())
        if hasattr(items, "__iter__"): # sequence -> ensure full form
            # Note: ID is actually a bitmask
            items = [cls.expand_enum_item(v, (1 << id) if enum_flag else (id+1)) for id, v in enumerate(items)]
            kwargs["items"] = items
            if enum_flag:
                value = (({value} if value else set()) if isinstance(value, str) else set(value))
            elif (not value) and items: # empty string -> user doesn't care
                value = items[0][0] # use first element as the default
        else: # function -> everything is ok
            value = None
        
        return value

# ===== FILE FILTERS ===== #

# Correspondence between use_filter_* in Python and FILE_TYPE_* in C: rna_def_fileselect_params()
# https://developer.blender.org/diffusion/B/browse/master/source/blender/makesrna/intern/rna_space.c

# Blender-file extensions: BLO_has_bfile_extension()
# https://developer.blender.org/diffusion/B/browse/master/source/blender/blenloader/intern/readfile.c

# Mapping between file extensions and FILE_TYPE_*: ED_path_extension_type()
# Checking if file name is a .blend backup: file_is_blend_backup()
# Mapping between FILE_TYPE_* and icons: filelist_geticon_ex()
# https://developer.blender.org/diffusion/B/browse/master/source/blender/editors/space_file/filelist.c

# Lists of file extensions for some file categories:
# https://developer.blender.org/diffusion/B/browse/master/source/blender/imbuf/intern/util.c

bpy_file_filters = { # in the order defined in ED_path_extension_type()
    "blender": [ # use_filter_blender, FILE_TYPE_BLENDER
        ".blend", ".ble", ".blend.gz", # BLO_has_bfile_extension()
    ],
    "backup": [ # use_filter_backup, FILE_TYPE_BLENDER_BACKUP
        ".blend?", ".blend??", # file_is_blend_backup()
    ],
    "app_bundle": [ # (no browser filter), FILE_TYPE_APPLICATIONBUNDLE
        ".app",
    ],
    "script": [ # use_filter_script, FILE_TYPE_PYSCRIPT
        ".py",
    ],
    "text": [ # use_filter_text, FILE_TYPE_TEXT
        ".txt", ".glsl", ".osl", ".data", ".pov", ".ini", ".mcr", ".inc",
    ],
    "font": [ # use_filter_font, FILE_TYPE_FTFONT
        ".ttf", ".ttc", ".pfb", ".otf", ".otc",
    ],
    "btx": [ # (no browser filter), FILE_TYPE_BTX
        ".btx",
    ],
    "collada": [ # (no browser filter), FILE_TYPE_COLLADA
        ".dae",
    ],
    "alembic": [ # (no browser filter), FILE_TYPE_ALEMBIC
        ".abc",
    ],
    "image": [ # use_filter_image, FILE_TYPE_IMAGE
	    ".png", ".tga",  ".bmp", ".jpg", ".jpeg", ".sgi", ".rgb", ".rgba",
	    ".tif", ".tiff", ".tx", # WITH_TIFF
	    ".jp2", ".j2c", # WITH_OPENJPEG
	    ".hdr", # WITH_HDR
	    ".dds", # WITH_DDS
	    ".dpx", ".cin", # WITH_CINEON
	    ".exr", # WITH_OPENEXR
	    ".psd", ".pdd",  ".psb", # WITH_OPENIMAGEIO
    ],
    "movie": [ # use_filter_movie, FILE_TYPE_MOVIE
	    ".avi", ".flc", ".mov", ".movie", ".mp4", ".m4v", ".m2v", ".m2t", ".m2ts", ".mts",
	    ".ts", ".mv", ".avs", ".wmv", ".ogv", ".ogg", ".r3d", ".dv", ".mpeg", ".mpg",
	    ".mpg2", ".vob", ".mkv", ".flv", ".divx", ".xvid", ".mxf", ".webm",
    ],
    "sound": [ # use_filter_sound, FILE_TYPE_SOUND
	    ".wav", ".ogg", ".oga", ".mp3", ".mp2", ".ac3", ".aac",
        ".flac", ".wma", ".eac3", ".aif", ".aiff", ".m4a", ".mka",
    ],
    "folder": [ # use_filter_folder, FILE_TYPE_FOLDER
    ],
    "blendid": [ # use_filter_blendid, FILE_TYPE_BLENDERLIB
    ],
}

bpy_file_icons = {
    "blender": 'FILE_BLEND',
    "backup": 'FILE_BACKUP',
    "app_bundle": 'UGLYPACKAGE',
    "script": 'FILE_SCRIPT',
    "text": 'FILE_TEXT',
    "font": 'FILE_FONT',
    "image": 'FILE_IMAGE',
    "movie": 'FILE_MOVIE',
    "sound": 'FILE_SOUND',
    "folder": 'FILE_FOLDER',
} # all others: FILE_BLANK / FILE

# ===== ENUMS ===== #

class BlEnums:
    extensible_classes = (bpy.types.PropertyGroup, bpy.types.ID, bpy.types.Bone, bpy.types.PoseBone) # see bpy_struct documentation
    
    common_attrs = {'bl_idname', 'bl_label', 'bl_description', 'bl_options',
        'bl_context', 'bl_region_type', 'bl_space_type', 'bl_category',
        'bl_use_postprocess', 'bl_use_preview', 'bl_use_shading_nodes',
        'is_animation', 'is_preview',
        'bl_width_default', 'bl_width_max', 'bl_width_min',
        'bl_height_default', 'bl_height_max', 'bl_height_min'}
    
    options = {tn:{item.identifier for item in getattr(bpy.types, tn).bl_rna.properties["bl_options"].enum_items}
        for tn in ("KeyingSet", "KeyingSetInfo", "KeyingSetPath", "Macro", "Operator", "Panel")
        if "bl_options" in getattr(bpy.types, tn).bl_rna.properties} # Since 2.73a, KeyingSet and KeyingSetPath don't have bl_options
    
    space_types = {item.identifier for item in bpy.types.Space.bl_rna.properties["type"].enum_items}
    region_types = {item.identifier for item in bpy.types.Region.bl_rna.properties["type"].enum_items}
    
    modes = {item.identifier for item in bpy.types.Context.bl_rna.properties["mode"].enum_items}
    paint_sculpt_modes = {'SCULPT', 'VERTEX_PAINT', 'PAINT_VERTEX',
        'WEIGHT_PAINT', 'PAINT_WEIGHT', 'TEXTURE_PAINT', 'PAINT_TEXTURE'}
    
    object_modes = {item.identifier for item in bpy.types.Object.bl_rna.properties["mode"].enum_items}
    object_types = {item.identifier for item in bpy.types.Object.bl_rna.properties["type"].enum_items}
    object_types_editable = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'ARMATURE', 'LATTICE', 'GPENCIL'}
    object_types_geometry = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}
    object_types_with_modifiers = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'LATTICE', 'GPENCIL'}
    object_types_with_vertices = {'MESH', 'CURVE', 'SURFACE', 'LATTICE', 'GPENCIL'}
    
    object_mode_support = {
        'MESH':{'OBJECT', 'EDIT', 'SCULPT', 'VERTEX_PAINT', 'WEIGHT_PAINT', 'TEXTURE_PAINT', 'PARTICLE_EDIT',
            'EDIT_MESH', 'PAINT_WEIGHT', 'PAINT_VERTEX', 'PAINT_TEXTURE', 'PARTICLE'},
        'CURVE':{'OBJECT', 'EDIT', 'EDIT_CURVE'},
        'SURFACE':{'OBJECT', 'EDIT', 'EDIT_SURFACE'},
        'META':{'OBJECT', 'EDIT', 'EDIT_METABALL'},
        'FONT':{'OBJECT', 'EDIT', 'EDIT_TEXT'},
        'ARMATURE':{'OBJECT', 'EDIT', 'POSE', 'EDIT_ARMATURE'},
        'LATTICE':{'OBJECT', 'EDIT', 'EDIT_LATTICE'},
        'GPENCIL':{'OBJECT', 'PAINT_GPENCIL', 'EDIT_GPENCIL', 'SCULPT_GPENCIL', 'WEIGHT_GPENCIL'},
        'EMPTY':{'OBJECT'},
        'CAMERA':{'OBJECT'},
        'LIGHT':{'OBJECT'},
        'LIGHT_PROBE':{'OBJECT'},
        'SPEAKER':{'OBJECT'},
    }
    mode_object_support = {
        'OBJECT':object_types,
        'EDIT':object_types_editable,
        'POSE':{'ARMATURE'},
        'SCULPT':{'MESH'},
        'VERTEX_PAINT':{'MESH'},
        'PAINT_VERTEX':{'MESH'},
        'WEIGHT_PAINT':{'MESH'},
        'PAINT_WEIGHT':{'MESH'},
        'TEXTURE_PAINT':{'MESH'},
        'PAINT_TEXTURE':{'MESH'},
        'PARTICLE_EDIT':{'MESH'},
        'PARTICLE':{'MESH'},
        'EDIT_MESH':{'MESH'},
        'EDIT_CURVE':{'CURVE'},
        'EDIT_SURFACE':{'SURFACE'},
        'EDIT_TEXT':{'FONT'},
        'EDIT_ARMATURE':{'ARMATURE'},
        'EDIT_METABALL':{'META'},
        'EDIT_LATTICE':{'LATTICE'},
        'PAINT_GPENCIL':{'GPENCIL'},
        'EDIT_GPENCIL':{'GPENCIL'},
        'SCULPT_GPENCIL':{'GPENCIL'},
        'WEIGHT_GPENCIL':{'GPENCIL'},
    },
    
    __generic_mode_map = {'OBJECT':'OBJECT', 'POSE':'POSE', 'SCULPT':'SCULPT', 'VERTEX_PAINT':'PAINT_VERTEX',
        'WEIGHT_PAINT':'PAINT_WEIGHT', 'TEXTURE_PAINT':'PAINT_TEXTURE', 'PARTICLE_EDIT':'PARTICLE'}
    __edit_mode_map = {'MESH':'EDIT_MESH', 'CURVE':'EDIT_CURVE', 'SURFACE':'EDIT_SURFACE',
        'META':'EDIT_METABALL', 'FONT':'EDIT_TEXT', 'ARMATURE':'EDIT_ARMATURE', 'LATTICE':'EDIT_LATTICE'}
    @classmethod
    def mode_from_object(cls, obj):
        if not obj: return 'OBJECT'
        return cls.__generic_mode_map.get(obj.mode) or cls.__edit_mode_map.get(obj.type)
    
    __mode_to_obj_map = {'EDIT_MESH':'EDIT', 'EDIT_CURVE':'EDIT', 'EDIT_SURFACE':'EDIT', 'EDIT_TEXT':'EDIT',
        'EDIT_ARMATURE':'EDIT', 'EDIT_METABALL':'EDIT', 'EDIT_LATTICE':'EDIT', 'POSE':'POSE', 'SCULPT':'SCULPT',
        'PAINT_WEIGHT':'WEIGHT_PAINT', 'PAINT_VERTEX':'VERTEX_PAINT', 'PAINT_TEXTURE':'TEXTURE_PAINT',
        'PARTICLE':'PARTICLE_EDIT', 'OBJECT':'OBJECT'}
    @classmethod
    def mode_to_object(cls, context_mode):
        return cls.__mode_to_obj_map.get(context_mode)
    
    @classmethod
    def normalize_mode(cls, mode, obj=None):
        if mode in cls.modes: return mode
        if mode == 'EDIT': return (cls.__edit_mode_map.get(obj.type) if obj else None)
        return cls.__generic_mode_map.get(mode)
    
    @classmethod
    def is_mode_valid(cls, mode, obj=None):
        return (mode in cls.object_mode_support[obj.type] if obj else mode == 'OBJECT')
    
    # Panel.bl_context is not a enum property, so we can't get all possible values through introspection
    panel_contexts = {
        'VIEW_3D':{
            "mesh_edit":'EDIT_MESH',
            "curve_edit":'EDIT_CURVE',
            "surface_edit":'EDIT_SURFACE',
            "text_edit":'EDIT_TEXT',
            "armature_edit":'EDIT_ARMATURE',
            "mball_edit":'EDIT_METABALL',
            "lattice_edit":'EDIT_LATTICE',
            "posemode":'POSE',
            "sculpt_mode":'SCULPT',
            "weightpaint":'PAINT_WEIGHT',
            "vertexpaint":'PAINT_VERTEX',
            "texturepaint":'PAINT_TEXTURE',
            "particlemode":'PARTICLE',
            "objectmode":'OBJECT',
        },
        'PROPERTIES':{
            "object", "data", "material", "physics", "constraint",
            "particle", "render", "scene", "texture", "world"
        },
    }
