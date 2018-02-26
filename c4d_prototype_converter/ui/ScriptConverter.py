
from __future__ import print_function
import c4d
import os
import nr.c4d.ui
import re
import shutil
import webbrowser
from .HelpMenu import HelpMenu
from .FileList import FileList
from ..little_jinja import little_jinja
from ..utils import makedirs
from .. import res, refactor

ID_SCRIPT_CONVERTER = 1040671


def get_library_scripts():
  dirs = [os.path.join(c4d.storage.GeGetC4DPath(x), 'scripts')
    for x in [c4d.C4D_PATH_LIBRARY, c4d.C4D_PATH_LIBRARY_USER]]
  dirs += os.getenv('C4D_SCRIPTS_DIR', '').split(os.pathsep)
  result = []
  def recurse(directory, depth=0):
    if not os.path.isdir(directory): return
    directory_index = len(result)
    for name in os.listdir(directory):
      path = os.path.join(directory, name)
      if name.endswith('.py'):
        name = '    ' * depth + name + '&i{}&'.format(c4d.RESOURCEIMAGE_PYTHONSCRIPT)
        result.append((path, name, True))
      elif os.path.isdir(path):
        recurse(path, depth+1)
    if len(result) != directory_index and depth > 0:
      name = '    ' * (depth-1) + os.path.basename(directory) + '/'
      name += '&i' + str(c4d.RESOURCEIMAGE_TIMELINE_FOLDER2)
      result.insert(directory_index, (directory, name, False))
  for dirname in dirs:
    dirname = dirname.strip()
    if dirname:
      recurse(dirname)
  return result


def refactor_command_script(code):
  code = refactor.indentation(code, '  ')  # To match the indentation of the plugin stub.
  code, docstring = refactor.split_docstring(code)
  code, exec_func = refactor.split_and_refactor_global_function(
    code, 'main', 'Execute', ['self', 'doc'], add_statement='return True')
  code, future_line = refactor.split_future_imports(code)

  if exec_func:
    member_code = exec_func
  else:
    lines = ['def Execute(self, doc):']
    for line in code.split('\n'):
      lines.append('  ' + line)
    lines.append('  return True')
    member_code = '\n'.join(lines)
    code = ''

  return {
    'future_line': future_line,
    'docstring': docstring,
    'code': code,
    'member_code': member_code
  }


class Converter(object):
  """
  Helper datastructure that contains all the information for converting
  scripts.
  """

  SCRIPT_FILE_METADATA_CACHE = {}

  @classmethod
  def get_script_file_metadata(cls, filename):
    if filename in cls.SCRIPT_FILE_METADATA_CACHE:
      return cls.SCRIPT_FILE_METADATA_CACHE[filename]
    if not os.path.isfile(filename):
      return {}
    with open(filename) as fp:
      code = fp.read()
    result = {}
    for field in re.findall('(Name-US|Description-US):(.*)$', code, re.M):
      if field[0] == 'Name-US':
        result['name'] = field[1].strip()
      elif field[0] == 'Description-US':
        result['description'] = field[1].strip()
    cls.SCRIPT_FILE_METADATA_CACHE[filename] = result
    return result

  def __init__(self, plugin_name, plugin_id, script_file, icon_file, directory, overwrite=True):
    self.plugin_name = plugin_name
    self.plugin_id = plugin_id
    self.script_file = script_file
    self.icon_file = icon_file
    self.directory = directory
    self.overwrite = overwrite
    self.plugin_help = None

  def autofill(self, default_plugin_name='My Plugin'):
    if not self.plugin_name:
      if self.script_file:
        metadata = self.get_script_file_metadata(self.script_file)
        if metadata.get('name'):
          self.plugin_name = metadata['name']
        else:
          self.plugin_name = os.path.splitext(os.path.basename(self.script_file))[0]
      else:
        self.plugin_name = default_plugin_name
    if not self.directory:
      write_dir = c4d.storage.GeGetC4DPath(c4d.C4D_PATH_STARTUPWRITE)
      dirname = re.sub('[^\w\d]+', '-', self.plugin_name).lower()
      self.directory = os.path.join(write_dir, 'plugins', dirname)
    if not self.plugin_help:
      metadata = self.get_script_file_metadata(self.script_file)
      self.plugin_help = metadata.get('description')
    if not self.icon_file:
      if self.script_file:
        for suffix in ('.tif', '.tiff', '.png', '.jpg'):
          self.icon_file = os.path.splitext(self.script_file)[0] + suffix
          if os.path.isfile(self.icon_file):
            break
        else:
          self.icon_file = None
    if not self.icon_file:
      self.icon_file = res.path('res/icons/default-icon.tiff')

  def files(self):
    parent_dir = self.directory or self.plugin_name
    plugin_filename = re.sub('[^\w\d]+', '-', self.plugin_name).lower()
    result = {
      'directory': parent_dir,
      'plugin': os.path.join(parent_dir, plugin_filename + '.pyp')
    }
    if self.icon_file:
      suffix = os.path.splitext(self.icon_file)[1]
      result['icon'] = os.path.join(parent_dir, 'res/icons/{0}{1}'.format(plugin_filename, suffix))
    return result

  def create(self):
    with open(self.script_file) as fp:
      code_parts = refactor_command_script(fp.read())
    # Indent the code appropriately for the plugin stub.
    member_code = '\n'.join('  ' + l for l in code_parts['member_code'].split('\n'))
    files = self.files()
    context = {
      'plugin_name': self.plugin_name,
      'plugin_id': self.plugin_id.strip(),
      'plugin_class': re.sub('[^\w\d]+', '', self.plugin_name),
      'plugin_icon': 'res/icons/' + os.path.basename(files['icon']) if files.get('icon') else None,
      'future_import': code_parts['future_line'],
      'global_code': code_parts['code'],
      'member_code': member_code,
      'plugin_help': self.plugin_help,
      'docstrings': code_parts['docstring']
    }
    with open(res.local('../templates/command_plugin.txt')) as fp:
      template = fp.read()
    if files.get('icon') and files.get('icon') != self.icon_file:
      makedirs(os.path.dirname(files['icon']))
      try:
        shutil.copy(self.icon_file, files['icon'])
      except shutil.Error as exc:
        print('Warning: Error copying icon:', exc)
    print('Creating', files['plugin'])
    makedirs(os.path.dirname(files['plugin']))
    with open(files['plugin'], 'w') as fp:
      fp.write(little_jinja(template, context))


class ScriptConverter(nr.c4d.ui.Component):

  def render(self, dialog):
    self.flush_children()
    self.load_xml_file('./ScriptConverter.xml')
    self.script_files = get_library_scripts()
    for key in ('script', 'script_file', 'plugin_name', 'plugin_help',
                'plugin_id', 'icon', 'directory', 'overwrite'):
      self[key].add_event_listener('value-changed', self.on_change)
    self['script'].pack(nr.c4d.ui.Item(delegate=self._fill_script_combobox))
    self['create'].add_event_listener('click', self.on_create)
    self['get_plugin_id'].add_event_listener('click', self.on_get_plugin_id)
    return super(ScriptConverter, self).render(dialog)

  def _fill_script_combobox(self, box):
    for i, (fn, name, _) in enumerate(self.script_files, 1):
      box.add(name)

  def init_values(self, dialog):
    self.on_change(None)

  def get_converter(self):
    if self['script'].active_index == 0:
      script_file = self['script_file'].value
    else:
      script_file = self.script_files[self['script'].active_index-1][0]
    return Converter(
      plugin_name = self['plugin_name'].value,
      plugin_id = self['plugin_id'].value,
      script_file = script_file,
      icon_file = self['icon'].value,
      directory = self['directory'].value,
      overwrite = self['overwrite'].value
    )

  def on_change(self, widget):
    visible = (self['script'].active_index == 0)
    item = self['script_file']
    item.visible = visible
    item.previous_sibling.visible = visible

    cnv = self.get_converter()
    cnv.autofill()
    files = cnv.files()
    parent = os.path.dirname(files.pop('directory'))
    enable_create = True

    self['filelist'].set_files(files, parent, set())
    self['filelist'].set_overwrite(cnv.overwrite)
    self['plugin_name'].set_helptext(cnv.plugin_name)
    self['plugin_help'].set_helptext(cnv.plugin_help)
    self['directory'].set_helptext(cnv.directory or '')

    if not cnv.plugin_id.isdigit():
      enable_create = enable_create and self['plugin_id'].enabled
      color = c4d.Vector(1.0, 0.3, 0.3)
    else:
      color = None
    self['plugin_id'].parent.previous_sibling.set_color(color)

    self['create'].enabled = enable_create

  def on_create(self, button):
    cnv = self.get_converter()
    cnv.autofill()
    try:
      cnv.create()
    except IOError as exc:
      c4d.gui.MessageDialog(str(exc))
    else:
      c4d.storage.ShowInFinder(cnv.files()['directory'])

  def on_get_plugin_id(self, button):
    webbrowser.open('http://www.plugincafe.com/forum/developer.asp')


window = nr.c4d.ui.DialogWindow(ScriptConverter(), title='Script Converter')
command = nr.c4d.ui.DialogOpenerCommand(ID_SCRIPT_CONVERTER, window)
command.Register('Script Converter...', c4d.PLUGINFLAG_HIDE)
