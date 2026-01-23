# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file LICENSE.rst or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION ${CMAKE_VERSION}) # this file comes with cmake

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "E:/mcp-dev/x64dbgMCP")
  file(MAKE_DIRECTORY "E:/mcp-dev/x64dbgMCP")
endif()
file(MAKE_DIRECTORY
  "E:/mcp-dev/x64dbgMCP/build/build32"
  "E:/mcp-dev/x64dbgMCP/build/plugin32-prefix"
  "E:/mcp-dev/x64dbgMCP/build/plugin32-prefix/tmp"
  "E:/mcp-dev/x64dbgMCP/build/plugin32-prefix/src/plugin32-stamp"
  "E:/mcp-dev/x64dbgMCP/build/plugin32-prefix/src"
  "E:/mcp-dev/x64dbgMCP/build/plugin32-prefix/src/plugin32-stamp"
)

set(configSubDirs Debug;Release;MinSizeRel;RelWithDebInfo)
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "E:/mcp-dev/x64dbgMCP/build/plugin32-prefix/src/plugin32-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "E:/mcp-dev/x64dbgMCP/build/plugin32-prefix/src/plugin32-stamp${cfgdir}") # cfgdir has leading slash
endif()
