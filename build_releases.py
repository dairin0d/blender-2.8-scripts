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

import os
import stat
import shutil
import datetime
import time

print()

copy_to_repositories = 0

repositories = {
}

this_path = os.path.dirname(__file__)
scripts_path = os.path.join(this_path, "scripts")
repositories_path = os.path.dirname(this_path)
releases_path = os.path.join(repositories_path, "releases", "addons-2.8")

addons_path = os.path.join(scripts_path, "addons")
modules_path = os.path.join(scripts_path, "modules")
modules_paths = [os.path.join(modules_path, name) for name in ("dairin0d",)]

ignore = shutil.ignore_patterns("__pycache__", "*.pyc")

# shutil.rmtree doesn't seem to handle well read-only files/directories
# https://stackoverflow.com/questions/2656322/shutil-rmtree-fails-on-windows-with-access-is-denied
def rmtree(top):
    for root, dirs, files in os.walk(top, topdown=False):
        for name in files:
            filename = os.path.join(root, name)
            os.chmod(filename, stat.S_IWUSR)
            os.remove(filename)
        time.sleep(0.05)
        for name in dirs:
            os.rmdir(os.path.join(root, name))
        time.sleep(0.05)
    os.rmdir(top)

def delete_file_or_directory(path):
    if os.path.isdir(path):
        rmtree(path)
    elif os.path.exists(path):
        os.remove(path)
    time.sleep(0.05)

def build_release(src_path, dst_path, make_zip=False, process_single_file=False):
    if os.path.isfile(src_path):
        if process_single_file: shutil.copyfile(src_path, dst_path)
        return
    
    if not os.path.isdir(src_path): return
    
    print((src_path, dst_path, make_zip))
    
    delete_file_or_directory(dst_path)
    
    shutil.copytree(src_path, dst_path, ignore=ignore)
    
    for module_path in modules_paths:
        module_name = os.path.basename(module_path)
        shutil.copytree(module_path, os.path.join(dst_path, module_name), ignore=ignore)
    
    if make_zip:
        root_dir = os.path.dirname(dst_path)
        base_dir = os.path.basename(dst_path)
        curr_dir = os.getcwd()
        os.chdir(dst_path)
        delete_file_or_directory(dst_path+".zip")
        shutil.make_archive(dst_path, "zip", root_dir, base_dir)
        os.chdir(curr_dir)

def build_releases():
    for dirname in os.listdir(addons_path):
        if dirname == "__pycache__": continue
        src_path = os.path.join(addons_path, dirname)
        dst_path = os.path.join(releases_path, dirname)
        build_release(src_path, dst_path, True)
        if copy_to_repositories and (dirname in repositories):
            dst_path = os.path.join(repositories_path, repositories[dirname], dirname)
            build_release(src_path, dst_path, process_single_file=True)

build_releases()
