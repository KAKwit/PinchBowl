application_id = 'dev.kakwit.PinchBowl'

desktop_file = configure_file(
  input: 'dev.kakwit.PinchBowl.desktop.in',
  output: 'dev.kakwit.PinchBowl.desktop',
  configuration: conf,
  install: true,
  install_dir: datadir / 'applications',
)

metainfo_file = configure_file(
  input: 'dev.kakwit.PinchBowl.metainfo.xml.in',
  output: 'dev.kakwit.PinchBowl.metainfo.xml',
  configuration: conf,
  install: true,
  install_dir: datadir / 'metainfo',
)

foreach size : ['16', '24', '32', '48', '64', '128', '256', '512']
  sizedir = '@0@x@0@'.format(size)
  install_data(
    'icons' / 'hicolor' / sizedir / 'apps' / 'dev.kakwit.PinchBowl.png',
    install_dir: datadir / 'icons' / 'hicolor' / sizedir / 'apps',
  )
endforeach

install_data(
  'icons/hicolor/symbolic/apps/dev.kakwit.PinchBowl-symbolic.svg',
  install_dir: datadir / 'icons/hicolor/symbolic/apps',
)
install_data(
  'branding/pinch-bowl-mark.png',
  install_dir: pkgdatadir / 'data' / 'branding',
)
