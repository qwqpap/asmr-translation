cmake_minimum_required(VERSION 3.24)
project(asmr_translation_native LANGUAGES CXX RC)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(NATIVE_SOURCES
    src/lrc.cpp
    src/media_player.cpp
    src/settings.cpp
    src/utf.cpp
    src/worker_client.cpp
    src/worker_protocol.cpp
)

add_executable(asmr-translation WIN32
    src/main.cpp
    src/lyrics_view.cpp
    ${NATIVE_SOURCES}
    resources/app.manifest
)
target_include_directories(asmr-translation PRIVATE include)
target_compile_definitions(asmr-translation PRIVATE
    UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN
)
target_compile_options(asmr-translation PRIVATE /W4 /permissive- /utf-8 /EHsc)
target_link_libraries(asmr-translation PRIVATE
    advapi32
    comctl32
    d2d1
    dwrite
    mf
    mfplat
    mfuuid
    ole32
    oleaut32
    propsys
    runtimeobject
    shell32
    shlwapi
    windowsapp
)
set_target_properties(asmr-translation PROPERTIES OUTPUT_NAME "asmr-translation")

enable_testing()
add_executable(native-tests
    tests/native_tests.cpp
    src/lrc.cpp
    src/media_player.cpp
    src/settings.cpp
    src/utf.cpp
    src/worker_protocol.cpp
)
target_include_directories(native-tests PRIVATE include)
target_compile_definitions(native-tests PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(native-tests PRIVATE /W4 /permissive- /utf-8 /EHsc)
target_link_libraries(native-tests PRIVATE
    advapi32
    mf
    mfplat
    mfuuid
    ole32
    oleaut32
    runtimeobject
    shell32
    shlwapi
    windowsapp
)
add_test(NAME native-tests COMMAND native-tests)
