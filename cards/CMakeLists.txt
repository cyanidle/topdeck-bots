cmake_minimum_required(VERSION 3.16)
project(cardscan LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(OpenCV 4 REQUIRED COMPONENTS core imgproc imgcodecs videoio img_hash ximgproc)

include(FetchContent)
FetchContent_Declare(
  ctre
  GIT_REPOSITORY https://github.com/hanickadot/compile-time-regular-expressions.git
  GIT_TAG v3.9.0
  GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(ctre)

find_path(TESSERACT_INCLUDE_DIR tesseract/capi.h)
find_library(TESSERACT_LIBRARY NAMES tesseract libtesseract.so.5 libtesseract.so)
if(NOT TESSERACT_LIBRARY)
  message(FATAL_ERROR "libtesseract runtime library not found (apt install libtesseract5)")
endif()
if(NOT TESSERACT_INCLUDE_DIR)
  set(TESSERACT_INCLUDE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/third_party/tesseract_capi)
  message(STATUS "libtesseract headers not found, using bundled C API declarations")
endif()

add_library(cardscan_core STATIC
  src/analyzer.cpp
  src/detector.cpp
  src/evidence.cpp
  src/geometry.cpp
  src/hashindex.cpp
  src/identity.cpp
  src/ocr.cpp
  src/ocrbench.cpp
  src/overlay.cpp
  src/phash.cpp
  src/report.cpp
  src/text_runs.cpp
  src/tracker.cpp
)
target_include_directories(cardscan_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${TESSERACT_INCLUDE_DIR}
  ${OpenCV_INCLUDE_DIRS}
)
target_link_libraries(cardscan_core PUBLIC ${OpenCV_LIBS} ${TESSERACT_LIBRARY} ctre::ctre)

add_library(cardscan_shared SHARED src/capi.cpp)
set_target_properties(cardscan_shared PROPERTIES OUTPUT_NAME cardscan)
target_link_libraries(cardscan_shared PRIVATE cardscan_core)
target_include_directories(cardscan_shared PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)

add_executable(cardscan_cli src/main.cpp)
set_target_properties(cardscan_cli PROPERTIES OUTPUT_NAME cardscan)
target_link_libraries(cardscan_cli PRIVATE cardscan_core)

enable_testing()
add_executable(cardscan_tests tests/test_main.cpp)
target_link_libraries(cardscan_tests PRIVATE cardscan_core)
add_test(NAME unit COMMAND cardscan_tests)

install(TARGETS cardscan_shared cardscan_cli
        RUNTIME DESTINATION bin
        LIBRARY DESTINATION lib)
install(FILES src/capi.h DESTINATION include)
