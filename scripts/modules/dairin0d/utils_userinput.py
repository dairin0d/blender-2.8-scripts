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

class InputKeyMonitor:
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
        if name.endswith(":ON"):
            return self.states.setdefault(name, False)
        elif name.endswith(":OFF"):
            return not self.states.setdefault(name, False)
        elif ":" not in name:
            return self.states.setdefault(name, False)
        else:
            return self.event == name
    
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
    
    def keychecker(self, shortcut, default_state=False):
        key_infos = self.get_keys(shortcut, self.invoke_key)
        state_infos = [info for info in key_infos if info.event in self._state_events]
        event_infos = [info for info in key_infos if info.event not in self._state_events]
        
        events_set = {info.full for info in event_infos}
        event_state = {"state": default_state, "counter": self.update_counter}
        
        def get_event_toggle(info):
            return self.event == info.full
        
        def get_state_toggle(info, mask_pos, mask_neg):
            state0 = int(self.prev_states.get(info.key, False))
            state1 = int(self.states.get(info.key, False))
            delta = state1 - state0
            if info.invert: delta = -delta
            return ((delta & mask_pos) > 0) or ((delta & mask_neg) < 0)
        
        def get_event_on(info):
            return event_state["state"]
        
        def get_state_on(info):
            return self.states.get(info.key, False) != info.invert
        
        def check(mode):
            if (self.event in events_set) and (self.update_counter != event_state["counter"]):
                event_state["state"] = not event_state["state"]
                event_state["counter"] = self.update_counter
            
            if mode == 'ON|TOGGLE':
                for info in state_infos:
                    if get_state_on(info): return 'ON'
                for info in event_infos:
                    if get_event_toggle(info): return 'TOGGLE'
                return None
            elif mode in {'TOGGLE', 'PRESS', 'RELEASE'}:
                mask_pos = (0 if mode == 'RELEASE' else -1)
                mask_neg = (0 if mode == 'PRESS' else -1)
                for info in state_infos:
                    if get_state_toggle(info, mask_pos, mask_neg): return True
                for info in event_infos:
                    if get_event_toggle(info): return True
                return False
            elif mode in {'ON', 'OFF'}:
                invert = (mode == 'OFF')
                for info in state_infos:
                    if get_state_on(info) != invert: return True
                for info in event_infos:
                    if get_event_on(info) != invert: return True
                return False
        
        return check
    
    class KeyInfo:
        __slots__ = ["full", "key", "event", "is_state", "invert"]
        def __init__(self, full):
            self.full = full
            self.key, self.event = full.split(":")
            self.is_state = (self.event == 'ON') or (self.event == 'OFF')
            self.invert = (self.event == 'OFF')
    
    _invoke_key = '<INVOKE_KEY>'
    _modifier_keys = {'shift', 'ctrl', 'alt', 'oskey'}
    _variant_modifiers = {'shift', 'ctrl', 'alt'}
    _variant_prefixes = ["LEFT_", "RIGHT_", "NDOF_BUTTON_"]
    _state_events = {'ON', 'OFF'}
    _keymap_keys = {item.identifier for item in bpy.types.KeyMapItem.bl_rna.properties["type"].enum_items} - {'NONE'}
    _keymap_events = {item.identifier for item in bpy.types.KeyMapItem.bl_rna.properties["value"].enum_items} - {'NOTHING'}
    _all_keys = _keymap_keys | _modifier_keys
    _all_events = _keymap_events | _state_events
    
    @classmethod
    def _iterate_key_variants(cls, key, events, invoke_key=None):
        if key == cls._invoke_key: key = invoke_key
        
        if key not in cls._all_keys: return
        
        is_keymap = not invoke_key
        
        all_events = (cls._keymap_events if is_keymap else cls._all_events)
        
        is_modifier = (key in cls._modifier_keys)
        is_variant_modifier = (key in cls._variant_modifiers)
        key_upper = key.upper()
        
        for event in events:
            if event not in all_events: continue
            
            if is_variant_modifier and (is_keymap or (event not in cls._state_events)):
                for prefix in cls._variant_prefixes:
                    yield f"{prefix}{key_upper}:{event}"
            elif is_keymap and is_modifier:
                yield f"{key_upper}:{event}"
            else:
                yield f"{key}:{event}"
    
    @classmethod
    def get_keys(cls, shortcut, invoke_key=None):
        if isinstance(shortcut, str): shortcut = cls.parse(shortcut)
        return [cls.KeyInfo(variant) for key, events in shortcut
                for variant in cls._iterate_key_variants(key, events, invoke_key)]
    
    @classmethod
    def parse(cls, shortcut):
        result = []
        
        for part in shortcut.split(","):
            subparts = [subpart.strip() for subpart in part.split(":")]
            result.append((subparts[0], set(subparts[1:])))
        
        return result

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
