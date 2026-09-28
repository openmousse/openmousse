Pod::Spec.new do |s|
  s.name           = 'MousseNative'
  s.version        = '1.0.0'
  s.summary        = 'OpenMousse native glue: extensions, Live Activities, edit menu'
  s.description    = 'App Group shared state for the share / widget / notification extensions, Live Activities and the Think input edit menu.'
  s.license        = 'AGPL-3.0-only'
  s.author         = 'OpenMousse'
  s.homepage       = 'https://openmousse.ai'
  s.platforms      = { :ios => '16.4' }
  s.swift_version  = '5.9'
  s.source         = { git: 'https://github.com/openmousse/openmousse.git' }
  s.static_framework = true

  s.dependency 'ExpoModulesCore'
  s.frameworks = 'WidgetKit', 'ActivityKit'

  s.source_files = "**/*.{h,m,swift}"
  s.pod_target_xcconfig = {
    'DEFINES_MODULE' => 'YES',
    'SWIFT_COMPILATION_MODE' => 'wholemodule'
  }
end
