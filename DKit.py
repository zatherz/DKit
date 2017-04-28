# -*- coding: utf-8 -*-

import sublime, sublime_plugin
from subprocess import Popen, PIPE, call
import os
import json
import sys
import functools
import re

plugin_settings = None
server_port = 9166
server_path = None
client_path = ""

server_process = None

ON_LOAD = sublime_plugin.all_callbacks['on_load']

def get_shell_args(args):
    return ' '.join(args)

def read_settings(key, default):
    global plugin_settings
    if plugin_settings is None:
        plugin_settings = sublime.load_settings('DKit.sublime-settings')

    return sublime.active_window().active_view().settings().get(key, plugin_settings.get(key, default))

def read_all_settings(key):
    global plugin_settings
    if plugin_settings is None:
        plugin_settings = sublime.load_settings('DKit.sublime-settings')

    result = plugin_settings.get(key, [])
    result.extend(sublime.active_window().active_view().settings().get(key, []))
    return result

def open_file(filename):
    if sys.platform == 'win32':
        os.startfile(filename)
    else:
        opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
        call([opener, filename])

def goto_offset(view, offset):
    region = sublime.Region(offset)
    view.sel().clear()
    view.sel().add(region)
    view.show_at_center(region)

def on_load(path=None, window=None, encoded_row_col=True, begin_edit=False):
    """Decorator to open or switch to a file.

    Opens and calls the "decorated function" for the file specified by path,
    or the current file if no path is specified. In the case of the former, if
    the file is open in another tab that tab will gain focus, otherwise the
    file will be opened in a new tab with a requisite delay to allow the file
    to open. In the latter case, the "decorated function" will be called on
    the currently open file.

    :param path: path to a file
    :param window: the window to open the file in
    :param encoded_row_col: the ``sublime.ENCODED_POSITION`` flag for
        ``sublime.Window.open_file``
    :param begin_edit: if editing the file being opened

    :returns: None
    """
    window = window or sublime.active_window()

    def wrapper(f):
        # if no path, tag is in current open file, return that
        if not path:
            return f(window.active_view())
        # else, open the relevant file
        view = window.open_file(os.path.normpath(path), encoded_row_col)

        def wrapped():
            # if editing the open file
            if begin_edit:
                with sublime.Edit(view):
                    f(view)
            else:
                f(view)

        # if buffer is still loading, wait for it to complete then proceed
        if view.is_loading():

            class set_on_load():
                callbacks = ON_LOAD

                def __init__(self):
                    # append self to callbacks
                    self.callbacks.append(self)

                def remove(self):
                    # remove self from callbacks, hence disconnecting it
                    self.callbacks.remove(self)

                def on_load(self, view):
                    # on file loading
                    try:
                        wrapped()
                    finally:
                        # disconnect callback
                        self.remove()

            set_on_load()
        # else just proceed (file was likely open already in another tab)
        else:
            wrapped()

    return wrapper

def start_server():
    global server_process
    global client_path

    out = call(client_path + " -q", shell=True, stdout=PIPE)
    if out == 0:
        print("Already running!")
        return

    global plugin_settings
    plugin_settings = sublime.load_settings('DKit.sublime-settings')

    global server_port
    server_port = read_settings('dcd_port', 9166)
    dcd_path = read_settings('dcd_path', '')

    global server_path
    server_path = os.path.join(dcd_path, 'dcd-server' + ('.exe' if sys.platform == 'win32' else ''))
    client_path = os.path.join(dcd_path, 'dcd-client' + ('.exe' if sys.platform == 'win32' else ''))
    server_path = sublime.expand_variables(server_path, sublime.active_window().extract_variables())
    client_path = sublime.expand_variables(client_path, sublime.active_window().extract_variables())

    if not os.path.exists(server_path):
        sublime.error_message('DCD server doesn\'t exist in the path specified:\n' + server_path + '\n\nSetup the path in DCD package settings and then restart sublime to get things working.')
        return False

    if not os.path.exists(client_path):
        sublime.error_message('DCD client doesn\'t exist in the path specified:\n' + client_path + '\n\nSetup the path in DCD package settings and then restart sublime to get things working.')
        return False

    include_paths = read_all_settings('include_paths')
    include_paths = ['-I"' + p + '"' for p in include_paths]

    args = ['"%s"' % server_path]
    args.extend(include_paths)
    args.extend(['-p' + str(server_port)])

    print('Restarting DCD server...')
    server_process = Popen(get_shell_args(args), shell=True)
    return True

def update_project(view, package_folder):
    dub = Popen(get_shell_args(['dub', 'describe']), stdin=PIPE, stdout=PIPE, shell=True, cwd=package_folder)
    description = dub.communicate()
    description = description[0].decode('utf-8')

    if description.startswith('Checking dependencies'):
        end_of_line = description.find('\n')
        description = description[end_of_line:]

    try:
        description = json.loads(description)
    except ValueError:
        sublime.error_message('Please run DUB at least once to figure out dependencies before trying again. Aborting.') #TODO: change into something more user-friendly
        return False

    include_paths = set()
    package_paths = []

    #new project set up
    project_window = view.window()
    project_settings = project_window.project_data()

    #if we have no project and no folder open, create new project data
    if project_settings is None:
        project_settings = {}
        project_settings['folders'] = []

    current_folders = set([folder['path'] for folder in project_settings['folders']])

    #get dub project info
    for index, package in enumerate(description['packages']):
        base_path = os.path.abspath(package['path'])

        #skip all but the top level package when suppressing folders
        if index == 0 or not read_settings("suppress_dependency_folders", False):
            if base_path not in current_folders:
                package_paths.append({'path': base_path, 'name': package['name']})

        for f in package['importPaths']:
            folder = os.path.join(base_path, f)
            include_paths.add(folder)
    settings = {'include_paths': [f for f in include_paths], 'package_file': view.file_name()}

    project_settings['folders'].extend(package_paths)
    project_settings.update({'settings': settings})

    #update the window with the new project data
    project_window.set_project_data(project_settings)

    return True

class DCD(sublime_plugin.EventListener):
    def __exit__(self, type, value, traceback):
        global server_process
        if not (server_process is None) and server_process.poll() is None:
            server_process.terminate()

    def on_window_command(self, window, command_name, args):
        if command_name == "exit":
            Popen(client_path + " --shutdown", shell=True)

    def on_query_completions(self, view, prefix, locations):
        if not view.scope_name(locations[0]).startswith('source.d'):
            return

        global server_process
        if server_process is None:
            start_server()

        position = locations[0]
        position = position - len(prefix)
        if (view.substr(position) != '.'):
            position = locations[0]

        response = self.request_completions(view.substr(sublime.Region(0, view.size())), position)
        return (response, sublime.INHIBIT_WORD_COMPLETIONS | sublime.INHIBIT_EXPLICIT_COMPLETIONS)

    def request_completions(self, file, position):
        args = ['"%s"' % client_path, '-c', str(position), '-p', str(server_port)]
        client = Popen(get_shell_args(args), stdin=PIPE, stdout=PIPE, shell=True)

        output = client.communicate(file.encode())
        output = output[0].decode('utf-8').splitlines()
        if len(output) == 0:
            return []

        completions_type = output[0]
        del output[0]

        if (completions_type == 'identifiers'):
            output = [self.parse_identifiers(line) for line in output]
        elif (completions_type == 'calltips'):
            output = [self.parse_calltips(line) for line in output]
        else:
            output = []

        return output

    def parse_identifiers(self, line):
        parts = line.split('\t')

        if len(parts) == 2:
            cmap = {
                'c': 'class',
                'i': 'interface',
                's': 'struct',
                'u': 'union',
                'v': 'variable',
                'm': 'member variable',
                'k': 'keyword',
                'f': 'function',
                'g': 'enum',
                'e': 'enum member',
                'P': 'package',
                'M': 'module',
                'a': 'array',
                'A': 'associative array',
                'l': 'alias',
                't': 'template',
                'T': 'mixin template'}

            visible_name = parts[0] + '\t' + cmap.get(parts[1], ' ')
            text = parts[0]

            return visible_name, text

        else:
            return None

    def parse_calltips(self, line):
        index = line.find('(')
        if index >= 0:
            visible_name = line
            text = line[index + 1 : -1]
        else:
            visible_name = line
            text = line
        return visible_name, text

class DcdStartServerCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        start_server()

class DcdUpdateIncludePathsCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        global client_path
        Popen(get_shell_args(['"%s"' % client_path, '--clearCache']), shell=True).wait()

        include_paths = set()
        for path in self.view.settings().get('include_paths', []):
            include_paths.add(path)

        if self.view.file_name():
            include_paths.add(os.path.dirname(self.view.file_name()))

        if len(include_paths) > 0:
            args = ['"%s"' % client_path]
            args.extend(['-I' + p for p in include_paths])

            Popen(get_shell_args(args), shell=True).wait()

class DcdGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        global client_path
        if (len(self.view.sel()) != 1):
            sublime.error_message('Please set the cursor on the token to check.')
            return

        pos = self.view.sel()[0].a + 1
        args = ['"%s"' % client_path, '--symbolLocation', '-c', str(pos), '-p"%s"' % server_port]

        client = Popen(get_shell_args(args), stdin=PIPE, stdout=PIPE, shell=True)
        contents = self.view.substr(sublime.Region(0, self.view.size()))
        output = client.communicate(contents.encode())
        output = output[0].decode('utf-8').strip()
        if len(output) == 0 or output == 'Not found':
            sublime.error_message('No symbol definition found.')
            return

        output = output.split('\t', 1)
        path = output[0].strip()
        offset = int(output[1].strip())

        if path == 'stdin':
            goto_offset(self.view, offset)
        else:
            @on_load(path)
            def and_then(view):
                sublime.set_timeout(functools.partial(goto_offset, view, offset), 10)


class DcdShowDocumentationCommand(sublime_plugin.TextCommand):
    _REGEX = re.compile(r'(?<!\\)\\('
                        '(?P<code>(\\\\)|([\\\'"abfnrtv]))|'
                        '(?P<oct>[0-7]{1,3})|'
                        'x(?P<hex>[0-9a-fA-F]{1,2})|'
                        'u(?P<uni>[0-9a-fA-F]{4})|'
                        'U(?P<UNI>[0-9a-fA-F]{8}))')

    def run(self, edit):
        global client_path
        if (len(self.view.sel()) != 1):
            sublime.error_message('Please set the cursor on the token to check.')
            return

        pos = self.view.sel()[0].a + 1
        args = ['"%s"' % client_path, '--doc', '-c', str(pos), '-p"%s"' % server_port]

        client = Popen(get_shell_args(args), stdin=PIPE, stdout=PIPE, shell=True)
        contents = self.view.substr(sublime.Region(0, self.view.size()))
        output = client.communicate(contents.encode())
        output = output[0].decode('utf-8').strip()
        if len(output) == 0 or output == 'Not found':
            sublime.error_message('No documentation found.')
            return

        docs = self._REGEX.sub(self._process_escape_codes, output.replace('\n', '\n\n'))

        panel = sublime.active_window().create_output_panel('ddoc')
        panel.insert(edit, 0, docs)
        sublime.active_window().run_command("show_panel", {"panel": "output.ddoc"})

    _ESCAPE_CODES = {
	'\\\\': '\\',
        '\\': '\\',
        "'": "'",
        '"': '"',
        'a': '\a',
        'f': '\f',
        'n': '\n',
        'r': '\r',
        't': '\t',
        'v': '\v',
    }

    def _process_escape_codes(self, match):
        c = match.group('code')
        if c is not None:
            return self._ESCAPE_CODES[c]
        o = match.group('oct')
        if c is not None:
            return chr(int(o, 8))
        x = match.group('hex') or match.group('uni') or match.group('UNI')
        if x is not None:
            return chr(int(o, 16))
        return match.group()

class DubListInstalledCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        try:
            dub = Popen(get_shell_args(['dub', 'list']), stdin=PIPE, stdout=PIPE, shell=True)
            output = dub.communicate()
            output = output[0].splitlines()
            del output[0]
            output = [o.decode('utf-8').strip().partition(': ')[0] for o in output]
            output = [o for o in output if len(o) > 0]
            self.view.window().show_quick_panel(output, None)
        except OSError:
            sublime.error_message('Unable to run DUB. Make sure that it is installed correctly and on your PATH environment variable')

class DubCreatePackageCommand(sublime_plugin.WindowCommand):
    def run(self):
        view = self.window.new_file()
        view.set_name('dub.json')
        view.set_syntax_file('Packages/JavaScript/JSON.tmLanguage')
        view.run_command('dub_create_package_text')

class DubCreatePackageTextCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        package = """{
  "name": "project-name",
  "description": "An example project skeleton",
  "homepage": "http://example.org",
  "copyright": "Copyright (c) 2014, Your Name",
  "authors": [],
  "dependencies": {},
  "targetType": "executable"
}"""
        self.view.insert(edit, 0, package)

class DubCreateProjectFromPackageCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        view = self.view
        if view.file_name():
            package_folder = os.path.dirname(view.file_name())
            package_file = os.path.basename(view.file_name())

            if package_file != 'dub.json' and package_file != 'dub.sdl' and package_file != 'package.json' :
                sublime.error_message('Please open the `dub.json`, `dub.sdl` or `package.json` file and then run the command again.')
                return
        else:
            sublime.error_message('Please open the `dub.json`, `dub.sdl` or `package.json` file and then run the command again.')
            return

        if update_project(view, package_folder):
            view.window().run_command('save_project_as')

class DubUpdateProjectCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        package_file = read_settings("package_file", None)
        if not package_file:
            sublime.error_message("The active project does not specify the path to the DUB package file.")
            return

        update_project(self.view, os.path.dirname(package_file))
