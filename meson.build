project(
  'pinch-bowl',
  version: '1.0.43',
  meson_version: '>= 1.2.0',
  default_options: ['warning_level=2'],
)

python = import('python').find_installation('python3')
gnome = import('gnome')

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
pkgdatadir = datadir / meson.project_name()
localedir = prefix / get_option('localedir')
application_id = 'dev.kakwit.PinchBowl'

conf = configuration_data()
conf.set('PYTHON', python.full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', localedir)
conf.set('pkgdatadir', pkgdatadir)
conf.set('application_id', application_id)

configure_file(
  input: 'src/pinchbowl.in',
  output: 'pinch-bowl',
  configuration: conf,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

install_subdir('src/pinchbowl', install_dir: pkgdatadir)
install_data('data/style.css', install_dir: pkgdatadir / 'data')
install_data(
  'data/sounds/alarm-clock-beep.wav',
  'data/sounds/ATTRIBUTION.txt',
  install_dir: pkgdatadir / 'data' / 'sounds',
)

subdir('data')
