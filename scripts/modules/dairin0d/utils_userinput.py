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

from collections import namedtuple

import bpy

#from bl_keymap_utils import keymap_hierarchy

from .utils_python import reverse_enumerate
from .bpy_inspect import BlRna

_all_modifiers = {'alt':"Alt", 'ctrl':"Ctrl", 'oskey':"OS Key", 'shift':"Shift"}

def _make_key_map(prop_name):
    enum_items = bpy.types.Event.bl_rna.properties[prop_name].enum_items
    return {name.replace(" ", "_").upper(): item.identifier for item in enum_items
            for name in (item.identifier, item.name) if name and name[0].isalnum()}

def _make_name_map():
    result = {}
    
    def get_name(item):
        if item.name.isalnum(): return item.name
        return item.identifier.replace("_", " ").capitalize()
    
    for item in bpy.types.Event.bl_rna.properties["type"].enum_items:
        result[item.identifier] = get_name(item)
    
    for item in bpy.types.Event.bl_rna.properties["value"].enum_items:
        result[item.identifier] = get_name(item)
    
    for identifier, name in _all_modifiers.items():
        result[identifier] = name
    
    return result

class InputKeyParser:
    invoke_key = "{INVOKEKEY}"
    keys = _make_key_map("type")
    events = _make_key_map("value")
    modifiers = {identifier:identifier for identifier in _all_modifiers.keys()}
    names = _make_name_map()
    
    KeyInfo = namedtuple("KeyInfo", ["type", "raw", "normalized", "invert"])
    
    @classmethod
    def normalize(cls, key, keyset, invoke_key=""):
        if isinstance(keyset, str):
            if keyset in ("keys", "events"):
                key = key.upper()
            elif keyset == "modifiers":
                key = key.lower()
            else:
                raise RuntimeError(f"Unsupported keyset attribute: {keyset}")
            keyset = getattr(cls, keyset)
        
        elements = key.replace("-", " ").split()
        combined0 = "".join(elements)
        combined1 = "_".join(elements)
        
        if invoke_key and (combined0 == cls.invoke_key): return invoke_key
        
        return keyset.get(combined0) or keyset.get(combined1) or ""
    
    @classmethod
    def parse(cls, keys_string, invoke_key=""):
        parts = [part.strip() for part in keys_string.split(":")]
        
        key_infos = []
        for key in (parts[0].split(",") if parts[0] else ()):
            key = key.strip()
            
            is_negative = key.startswith("!")
            if is_negative: key = key[1:].strip()
            
            event_type_normalized = cls.normalize(key, "keys", invoke_key)
            if event_type_normalized:
                key_infos.append(cls.KeyInfo('EVENT_TYPE', key, event_type_normalized, is_negative))
                continue
            
            modifier_normalized = cls.normalize(key, "modifiers")
            if modifier_normalized:
                key_infos.append(cls.KeyInfo('MODIFIER', key, modifier_normalized, is_negative))
                continue
            
            key_infos.append(cls.KeyInfo('UNKNOWN', key, "", is_negative))
        
        event_infos = []
        for event_value in parts[1:]:
            event_value_normalized = cls.normalize(event_value, "events")
            if event_value_normalized or (not event_value):
                event_infos.append(cls.KeyInfo('EVENT_VALUE', event_value, event_value_normalized, False))
            else:
                event_infos.append(cls.KeyInfo('UNKNOWN', event_value, "", False))
        
        return key_infos, event_infos
    
    @classmethod
    def validate(cls, keys_string, invoke_key="", can_invert=True):
        key_infos, event_infos = cls.parse(keys_string, invoke_key)
        
        for event_info in event_infos:
            if event_info.type == 'UNKNOWN': return False
        
        for key_info in key_infos:
            if key_info.type == 'UNKNOWN': return False
            if (not can_invert) and key_info.invert: return False
        
        return True

class InputKeyMonitor:
    all_keys = InputKeyParser.keys
    all_events = InputKeyParser.events
    all_modifiers = InputKeyParser.modifiers
    
    def __init__(self, event=None):
        self.event = ""
        self.prev_states = {}
        self.states = {}
        self.invoke_key = 'NONE'
        self.invoke_event = 'NONE'
        self.update_counter = 0
        
        if event is not None:
            self.invoke_key = event.type
            self.invoke_event = event.value
            self.update(event)
    
    def __getitem__(self, name):
        if name.startswith("!"): return not self[name[1:]]
        if ":" in name: return self.event == name
        return self.states.setdefault(name, False)
    
    def __setitem__(self, name, state):
        self.states[name] = state
    
    def update(self, event):
        self.update_counter += 1
        
        self.prev_states.update(self.states)
        
        if (event.value == 'PRESS') or (event.value == 'DOUBLE_CLICK'):
            self.states[event.type] = True
        elif event.value == 'RELEASE':
            self.states[event.type] = False
        
        self.states['alt'] = event.alt
        self.states['ctrl'] = event.ctrl
        self.states['oskey'] = event.oskey
        self.states['shift'] = event.shift
        
        self.event = event.type+":"+event.value
    
    def keychecker(self, keys, default_state=False):
        keys = self.parse_keys(keys)
        
        events_set = {key for key in keys if ":" in key}
        event_state = {"state": default_state, "counter": self.update_counter}
        
        def get_event_toggle(key):
            invert = key.startswith("!")
            if invert: key = key[1:]
            return self.event == key
        
        def get_state_toggle(key, mask_pos, mask_neg):
            invert = key.startswith("!")
            if invert: key = key[1:]
            state0 = int(self.prev_states.get(key, False))
            state1 = int(self.states.get(key, False))
            delta = state1 - state0
            if invert: delta = -delta
            return ((delta & mask_pos) > 0) or ((delta & mask_neg) < 0)
        
        def get_event_on(key):
            invert = key.startswith("!")
            if invert: key = key[1:]
            return event_state["state"]
        
        def get_state_on(key):
            invert = key.startswith("!")
            if invert: key = key[1:]
            return self.states.get(key, False) != invert
        
        def check(mode=None):
            if (self.event in events_set) and (self.update_counter != event_state["counter"]):
                event_state["state"] = not event_state["state"]
                event_state["counter"] = self.update_counter
            
            if mode is None: return any(self[key] for key in keys) # old logic
            
            if mode == 'ON|TOGGLE':
                for key in keys:
                    if (":" not in key) and get_state_on(key): return 'ON'
                for key in keys:
                    if (":" in key) and get_event_toggle(key): return 'TOGGLE'
                return None
            elif mode in {'TOGGLE', 'PRESS', 'RELEASE'}:
                mask_pos = (0 if mode == 'RELEASE' else -1)
                mask_neg = (0 if mode == 'PRESS' else -1)
                for key in keys:
                    if ":" in key:
                        if get_event_toggle(key): return True
                    else:
                        if get_state_toggle(key, mask_pos, mask_neg): return True
                return False
            elif mode in {'ON', 'OFF'}:
                invert = (mode == 'OFF')
                for key in keys:
                    if ":" in key:
                        state = get_event_on(key)
                    else:
                        state = get_state_on(key)
                    if state != invert: return True
                return False
        
        check.is_event = ((":" in keys[0]) if keys else False)
        
        return check
    
    def combine_key_parts(self, key, keyset, use_invoke_key=False):
        elements = key.split()
        combined0 = "".join(elements)
        combined1 = "_".join(elements)
        
        if use_invoke_key and (combined0 == "{INVOKEKEY}"):
            return self.invoke_key
        
        return keyset.get(combined0) or keyset.get(combined1) or ""
    
    def parse_keys(self, keys_string):
        parts = keys_string.split(":")
        keys_string = parts[0]
        
        event_id = ""
        if len(parts) > 1:
            event_id = self.combine_key_parts(parts[1].upper(), self.all_events)
            if event_id: event_id = ":"+event_id
        
        keys = []
        for key in keys_string.split(","):
            key = key.strip()
            
            is_negative = key.startswith("!")
            prefix = ""
            if is_negative:
                key = key[1:]
                prefix = "!"
            
            key_id = self.combine_key_parts(key.upper(), self.all_keys, True)
            modifier_id = self.combine_key_parts(key.lower(), self.all_modifiers)
            
            if key_id:
                keys.append(prefix+key_id+event_id)
            elif modifier_id:
                if len(event_id) != 0:
                    modifier_id = modifier_id.upper()
                    if modifier_id == 'OSKEY': # has no left/right/ndof variants
                        keys.append(prefix+modifier_id+event_id)
                    else:
                        keys.append(prefix+"LEFT_"+modifier_id+event_id)
                        keys.append(prefix+"RIGHT_"+modifier_id+event_id)
                        keys.append(prefix+"NDOF_BUTTON_"+modifier_id+event_id)
                else:
                    keys.append(prefix+modifier_id)
        
        return keys

class ModeStack:
    def __init__(self, keys, transitions, default_mode, mode=None, search_direction=-1):
        self.keys = keys
        self.prev_state = {}
        self.transitions = set(transitions)
        self.mode = (default_mode if mode is None else mode)
        self.default_mode = default_mode
        self.stack = [self.default_mode] # default mode should always be in the stack!
        self.search_direction = search_direction
    
    def update(self):
        for name, keychecker in self.keys.items():
            result = keychecker('ON|TOGGLE')
            
            if result == 'TOGGLE':
                delta_on = (1 if self.mode != name else -1)
            else:
                is_on = int(bool(result))
                delta_on = is_on - self.prev_state.get(name, 0)
                self.prev_state[name] = is_on
            
            if delta_on > 0:
                if self.transition_allowed(self.mode, name):
                    self.remove(name)
                    self.stack.append(name) # move to top
                    self.mode = name
            elif delta_on < 0:
                if self.mode != name:
                    self.remove(name)
                else:
                    self.find_transition()
    
    def remove(self, name):
        if name in self.stack:
            self.stack.remove(name)
    
    def find_transition(self):
        if self.search_direction < 0:
            indices = range(len(self.stack)-1, -1, -1)
        else:
            indices = range(len(self.stack))
        
        for i in indices:
            name = self.stack[i]
            if self.transition_allowed(self.mode, name):
                self.mode = name
                self.stack = self.stack[:i+1]
                break
    
    def transition_allowed(self, mode0, mode1):
        is_allowed = (mode0+":"+mode1) in self.transitions
        is_allowed |= (mode1+":"+mode0) in self.transitions
        return is_allowed
    
    def add_transitions(self, transitions):
        self.transitions.update(transitions)
    
    def remove_transitions(self, transitions):
        self.transitions.difference_update(transitions)

class KeyMapUtils:
    @staticmethod
    def search(idname, place=None):
        """Iterate over keymap items with given idname. Yields tuples (keyconfig, keymap, keymap item)"""
        place_is_str = isinstance(place, str)
        keymaps = None
        keyconfigs = bpy.context.window_manager.keyconfigs
        if isinstance(place, bpy.types.KeyMap):
            keymaps = (place,)
            keyconfigs = (next((kc for kc in keyconfigs if place.name in kc), None),)
        elif isinstance(place, bpy.types.KeyConfig):
            keyconfigs = (place,)
        
        for kc in keyconfigs:
            for km in keymaps or kc.keymaps:
                if place_is_str and (km.name != place):
                    continue
                for kmi in km.keymap_items:
                    if kmi.idname == idname:
                        yield (kc, km, kmi)
    
    @staticmethod
    def exists(idname, place=None):
        return bool(next(KeyMapUtils.search(idname), False))
    
    @staticmethod
    def set_active(idname, active, place=None):
        for kc, km, kmi in KeyMapUtils.search(idname, place):
            kmi.active = active
    
    @staticmethod
    def remove(idname, user_defined=True, user_modified=True, place=None):
        for kc, km, kmi in list(KeyMapUtils.search(idname, place)):
            if (not user_defined) and kmi.is_user_defined:
                continue
            if (not user_modified) and kmi.is_user_modified:
                continue
            km.keymap_items.remove(kmi)
    
    @staticmethod
    def index(km, idname):
        for i, kmi in enumerate(km.keymap_items):
            if kmi.idname == idname:
                return i
        return -1
    
    @staticmethod
    def normalize_event_type(event_type):
        if event_type == 'ACTIONMOUSE':
            userprefs = bpy.context.user_preferences
            select_mouse = userprefs.inputs.select_mouse
            return ('RIGHTMOUSE' if select_mouse == 'LEFT' else 'LEFTMOUSE')
        elif event_type == 'SELECTMOUSE':
            userprefs = bpy.context.user_preferences
            select_mouse = userprefs.inputs.select_mouse
            return ('LEFTMOUSE' if select_mouse == 'LEFT' else 'RIGHTMOUSE')
        return event_type
    
    @staticmethod
    def equal(kmi, event, pressed_keys=[]):
        """Test if event corresponds to the given keymap item"""
        modifier_match = (kmi.key_modifier == 'NONE') or (kmi.key_modifier in pressed_keys)
        modifier_match &= kmi.any or ((kmi.alt == event.alt) and (kmi.ctrl == event.ctrl)
            and (kmi.shift == event.shift) and (kmi.oskey == event.oskey))
        kmi_type = KeyMapUtils.normalize_event_type(kmi.type)
        event_type = KeyMapUtils.normalize_event_type(event.type)
        return ((kmi_type == event_type) and (kmi.value == event.value) and modifier_match)
    
    @staticmethod
    def clear(ko):
        if isinstance(ko, bpy.types.KeyMap):
            ko = ko.keymap_items
        elif isinstance(ko, bpy.types.KeyConfig):
            ko = ko.keymaps
        elif isinstance(ko, bpy.types.WindowManager):
            ko = ko.keyconfigs
        
        while len(ko) != 0:
            ko.remove(ko[0])
    
    @staticmethod
    def serialize(ko):
        if isinstance(ko, bpy.types.KeyMapItem):
            kmi = ko # also: kmi.map_type ? (seems that it's purely derivative)
            return dict(idname=kmi.idname, propvalue=kmi.propvalue,
                type=kmi.type, value=kmi.value, any=kmi.any,
                shift=kmi.shift, ctrl=kmi.ctrl, alt=kmi.alt,
                oskey=kmi.oskey, key_modifier=kmi.key_modifier,
                active=kmi.active, show_expanded=kmi.show_expanded,
                id=kmi.id, properties=BlRna.serialize(kmi.properties, ignore_default=True))
        elif isinstance(ko, bpy.types.KeyMap):
            km = ko
            return dict(name=km.name, space_type=km.space_type, region_type=km.region_type,
                is_modal=km.is_modal, is_user_modified=km.is_user_modified,
                show_expanded_children=km.show_expanded_children,
                keymap_items=[KeyMapUtils.serialize(kmi) for kmi in km.keymap_items])
        elif isinstance(ko, bpy.types.KeyConfig):
            kc = ko
            return dict(name=kc.name, keymaps=[KeyMapUtils.serialize(km) for km in kc.keymaps])
    
    @staticmethod
    def deserialize(ko, data, head=False):
        # keymap_items / keymaps / keyconfigs are reported as just "bpy_prop_collection" type
        if isinstance(ko, bpy.types.KeyMap):
            if ko.is_modal:
                kmi = ko.keymap_items.new_modal(data["propvalue"], data["type"], data["value"], any=data.get("any", False),
                    shift=data.get("shift", False), ctrl=data.get("ctrl", False), alt=data.get("alt", False),
                    oskey=data.get("oskey", False), key_modifier=data.get("key_modifier", 'NONE'))
            else:
                kmi = ko.keymap_items.new(data["idname"], data["type"], data["value"], any=data.get("any", False),
                    shift=data.get("shift", False), ctrl=data.get("ctrl", False), alt=data.get("alt", False),
                    oskey=data.get("oskey", False), key_modifier=data.get("key_modifier", 'NONE'), head=head)
            kmi.active = data.get("active", True)
            kmi.show_expanded = data.get("show_expanded", False)
            BlRna.deserialize(kmi.properties, data.get("properties", {}), suppress_errors=True)
        elif isinstance(ko, bpy.types.KeyConfig):
            # Note: for different modes, different space_type are required!
            # e.g. 'VIEW_3D' for "3D View", and 'EMPTY' for "Sculpt"
            km = ko.keymaps.new(data["name"], space_type=data.get("space_type", 'EMPTY'),
                region_type=data.get("region_type", 'WINDOW'), modal=data.get("is_modal", False))
            km.is_user_modified = data.get("is_user_modified", False)
            km.show_expanded_children = data.get("show_expanded_children", False)
            for kmi_data in data.get("keymap_items", []):
                KeyMapUtils.deserialize(km, kmi_data)
        elif isinstance(ko, bpy.types.WindowManager):
            kc = ko.keyconfigs.new(data["name"])
            for km_data in data.get("keymaps", []):
                KeyMapUtils.deserialize(kc, km_data)
    
    @staticmethod
    def insert(km, kmi_datas):
        if not kmi_datas:
            return
        
        km_items = [KeyMapUtils.serialize(kmi) for kmi in km.keymap_items]
        
        def insertion_index(idnames, to_end):
            if "*" in idnames:
                return (len(km_items)-1 if to_end else 0)
            for i, kmi_data in (reverse_enumerate(km_items) if to_end else enumerate(km_items)):
                if kmi_data["idname"] in idnames:
                    return i
            return None
        
        src_count = len(km.keymap_items)
        only_append = True
        for after, kmi_data, before in kmi_datas:
            i_after = (insertion_index(after, True) if after else None)
            i_before = (insertion_index(before, False) if before else None)
            
            if (i_before is None) and (i_after is None):
                i = len(km_items)
            elif i_before is None:
                i = i_after+1
            elif i_after is None:
                i = i_before
            else:
                i = (i_after+1 if "*" not in after else i_before)
            
            only_append &= (i >= src_count)
            
            km_items.insert(i, kmi_data)
        
        if only_append:
            for kmi_data in km_items[src_count:]:
                KeyMapUtils.deserialize(km, kmi_data)
        else:
            KeyMapUtils.clear(km)
            for kmi_data in km_items:
                KeyMapUtils.deserialize(km, kmi_data)
