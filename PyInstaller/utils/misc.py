#-----------------------------------------------------------------------------
# Copyright (c) 2013, PyInstaller Development Team.
#
# Distributed under the terms of the GNU General Public License with exception
# for distributing bootloader.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------


"""
This module is for the miscellaneous routines which do not fit somewhere else.
"""

import glob
import imp
import os
import py_compile
import sys

from PyInstaller import log as logging
from PyInstaller.compat import is_unix, is_win

logger = logging.getLogger(__name__)


def dlls_in_subdirs(directory):
    """Returns *.dll, *.so, *.dylib in given directories and subdirectories."""
    filelist = []
    for root, dirs, files in os.walk(directory):
        filelist.extend(dlls_in_dir(root))
    return filelist


def dlls_in_dir(directory):
    """Returns *.dll, *.so, *.dylib in given directory."""
    files = []
    files.extend(glob.glob(os.path.join(directory, '*.so')))
    files.extend(glob.glob(os.path.join(directory, '*.dll')))
    files.extend(glob.glob(os.path.join(directory, '*.dylib')))
    return files


def find_executable(executable, path=None):
    """
    Try to find 'executable' in the directories listed in 'path' (a
    string listing directories separated by 'os.pathsep'; defaults to
    os.environ['PATH']).

    Returns the complete filename or None if not found.

    Code from http://snippets.dzone.com/posts/show/6313
    """
    if path is None:
        path = os.environ['PATH']
    paths = path.split(os.pathsep)
    extlist = ['']

    if is_win:
        (base, ext) = os.path.splitext(executable)
        # Executable files on windows have an arbitrary extension, but
        # .exe is automatically appended if not present in the name.
        if not ext:
            executable = executable + ".exe"
        pathext = os.environ['PATHEXT'].lower().split(os.pathsep)
        (base, ext) = os.path.splitext(executable)
        if ext.lower() not in pathext:
            extlist = pathext

    for ext in extlist:
        execname = executable + ext
        if os.path.isfile(execname):
            return execname
        else:
            for p in paths:
                f = os.path.join(p, execname)
                if os.path.isfile(f):
                    return f
    else:
        return None


def get_unicode_modules():
    """
    Try importing codecs and encodings to include unicode support
    in created binary.
    """
    modules = []
    try:
        import codecs
        modules.append('codecs')
        # `encodings` imports `codecs`, so only the first is required.
        import encodings
        modules.append('encodings')
    except ImportError:
        logger.error("Cannot detect modules 'codecs' and 'encodings'.")

    return modules


def get_code_object(filename, new_filename=None):
    """
    Convert source code from Python source file to code object.

        new_filename  File name that the code should be compiled with.
    """
    try:
        # with statement will close the file automatically.
        with open(filename, 'rU') as fp:
            source_code_string = fp.read() + '\n'
        # Sometimes you might need to change the filename in the code object.
        if new_filename:
            filename = new_filename
        code_object = compile(source_code_string, filename, 'exec', 0, True)
        return code_object
    except SyntaxError as e:
        logger.exception(e)
        raise SystemExit(10)


def get_path_to_toplevel_modules(filename):
    """
    Return the path to top-level directory that contains Python modules.

    It will look in parent directories for __init__.py files. The first parent
    directory without __init__.py is the top-level directory.

    Returned directory might be used to extend the PYTHONPATH.
    """
    curr_dir = os.path.dirname(os.path.abspath(filename))
    pattern = '__init__.py'

    # Try max. 10 levels up.
    try:
        for i in range(10):
            files = set(os.listdir(curr_dir))
            # 'curr_dir' is still not top-leve go to parent dir.
            if pattern in files:
                curr_dir = os.path.dirname(curr_dir)
            # Top-level dir found - return it.
            else:
                return curr_dir
    except IOError:
        pass
    # No top-level directory found or any error.
    return None


def check_not_running_as_root():
    """
    Raise SystemExit error if the user is on unix and trying running
    PyInstaller or its utilities as superuser 'root'.
    """
    if is_unix:
        # Prevent running as superuser (root).
        if hasattr(os, "getuid") and os.getuid() == 0:
            logger.error('You are running PyInstaller as user root.'
                ' This is not supported.')
            raise SystemExit(10)


def mtime(fnm):
    try:
        # TODO: explain why this doesn't use os.path.getmtime() ?
        #       - It is probably not used because it returns fload and not int.
        return os.stat(fnm)[8]
    except:
        return 0


def compile_py_files(toc, workpath):
    """
    Given a TOC or equivalent list of tuples, generates all the required
    pyc/pyo files, writing in a local directory if required, and returns the
    list of tuples with the updated pathnames.
    
    In the old system using ImpTracker, the generated TOC of "pure" modules
    already contains paths to nm.pyc or nm.pyo and it is only necessary
    to check that these files are not older than the source.
    In the new system using ModuleGraph, the path given is to nm.py
    and we do not know if nm.pyc/.pyo exists. The following logic works
    with both (so if at some time modulegraph starts returning filenames
    of .pyc, it will cope).
    """

    # For those modules that need to be rebuilt, use the build directory
    # PyInstaller creates during the build process.
    basepath = os.path.join(workpath, "localpycos")

    # Copy everything from toc to this new TOC, possibly unchanged.
    new_toc = []
    for (nm, fnm, typ) in toc:
        # Keep unrelevant items unchanged.
        if typ != 'PYMODULE':
            new_toc.append((nm, fnm, typ))
            continue

        if fnm.endswith('.py') :
            # we are given a source path, determine the object path if any
            src_fnm = fnm
            # assume we want pyo only when now running -O or -OO
            obj_fnm = src_fnm + ('o' if sys.flags.optimize else 'c')
            if not os.path.exists(obj_fnm) :
                # alas that one is not there so assume the other choice
                obj_fnm = src_fnm + ('c' if sys.flags.optimize else 'o')
        else:
            # fnm is not "name.py" so assume we are given name.pyc/.pyo
            obj_fnm = fnm # take that namae to be the desired object
            src_fnm = fnm[:-1] # drop the 'c' or 'o' to make a source name

        # We need to perform a build ourselves if obj_fnm doesn't exist,
        # or if src_fnm is newer than obj_fnm, or if obj_fnm was created
        # by a different Python version.
        # TODO: explain why this does read()[:4] (reading all the file)
        # instead of just read(4)? Yes for many a .pyc file, it is all
        # in one sector so there's no difference in I/O but still it
        # seems inelegant to copy it all then subscript 4 bytes.
        needs_compile = ( (mtime(src_fnm) > mtime(obj_fnm) )
                          or
                          (open(obj_fnm, 'rb').read()[:4] != imp.get_magic())
                        )
        if needs_compile:
            try:
                # TODO: there should be no need to repeat the compile,
                # because ModuleGraph does a compile and stores the result
                # in the .code member of the graph node. Should be possible
                # to get the node and write the code to obj_fnm
                py_compile.compile(src_fnm, obj_fnm)
                logger.debug("compiled %s", src_fnm)
            except IOError:
                pass
                # If we're compiling on a system directory, probably we don't
                # have write permissions; thus we compile to a local directory
                # and change the TOC entry accordingly.
                ext = os.path.splitext(obj_fnm)[1]

                if "__init__" not in obj_fnm:
                    # If it's a normal module, use last part of the qualified
                    # name as module name and the first as leading path
                    leading, mod_name = nm.split(".")[:-1], nm.split(".")[-1]
                else:
                    # In case of a __init__ module, use all the qualified name
                    # as leading path and use "__init__" as the module name
                    leading, mod_name = nm.split("."), "__init__"

                leading = os.path.join(basepath, *leading)

                if not os.path.exists(leading):
                    os.makedirs(leading)

                obj_fnm = os.path.join(leading, mod_name + ext)
                # TODO see above regarding read()[:4] versus read(4)
                needs_compile = (mtime(src_fnm) > mtime(obj_fnm)
                                 or
                                 open(obj_fnm, 'rb').read()[:4] != imp.get_magic())
                if needs_compile:
                    # TODO see above regarding using node.code
                    py_compile.compile(src_fnm, fnm)
                    logger.debug("compiled %s", src_fnm)
        # if we get to here, obj_fnm is the path to the compiled module nm.py
        new_toc.append((nm, obj_fnm, typ))

    return new_toc
