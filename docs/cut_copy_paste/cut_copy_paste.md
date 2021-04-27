# Cut / Copy / Paste

## Brief overview
This addon provides an advanced copy/paste functionality for 3D objects and edit-mode elements.

When registered, it will add the copy-pasting shortcuts to the keymaps. It will also add a "Copy/Paste" sub-menu to the 3D view's "Object" menu (and its equivalents in edit modes), as well as to the object-mode context menu.

## Features

### Copy
Copies the selection. Can be invoked in one of the following modes:
* **Copy**: saves the selection to a clipboard file (located in the temporary directory), and stores metadata (including information about the library paths) in the system's text clipboard.
* **Copy as Text**: packs the clipboard file into the system's text clipboard, and doesn't include information about the library paths. Useful for sending the copied items over the Internet, and/or when you want to make sure the pasted items won't reference any external files.
* **Copy Linked**: doesn't write a clipboard file. Useful if you only want to link objects from the source file and want to avoid the unnecessary operations.

### Paste
In Object mode, inserts the copied objects into the active scene. In Edit mode, inserts the copied elements into the active object (will try to perform object type conversion where possible). After a Paste operation is performed, you can adjust the following parameters in the [operator redo panel](https://docs.blender.org/manual/en/latest/interface/undo_redo.html):
* **Mode**: whether to append or link the pasted objects. If linking is impossible, objects will be appended regardless of this setting.
* **Collection**: in which collection to put the pasted objects. Can be one of the following:
  * *Scene*: put objects directly into the current scene's collection.
  * *First*: put objects into the first sub-collection of the current scene.
  * *Active*: put objects into the same collection(s) as the active object (if these collections are in the current scene).
  * *New*: put objects into a new collection.
* **At Cursor**: move the pasted objects / elements to the 3D cursor (using the current Pivot Point settings).
* **Link Relative**: when linking, use relative paths.
* **Select**: select the pasted objects/elements (will deselect everything else).
* **Replace**: replace the current selection with the pasted objects/elements (i.e. delete the current selection before pasting).
* **Reuse**: try to use the existing materials / textures / images where possible (note: this can be slow).
* **Frame Offset**: whether to offset the pasted Grease Pencil frames to the current frame.

### Cut
Copies the selected objects or elements to the clipboard and immediately deletes them.

## Preferences
In the addon preferences, you can select the object modes for which the shortcuts and the sub-menus will be active.
You can also customize the number of shortcuts, their key bindings and which operations they perform.
