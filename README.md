# x64dbg MCP - Optimized

An optimized Model Context Protocol (MCP) server for x64dbg debugger, enabling AI-assisted reverse engineering.

## Features

- **23 Optimized Tools** - Consolidated from 40+ tools for reduced token usage
- **Batch Operations** - Get full CPU context in ONE call instead of 25+
- **Cross-Architecture** - Works with both x64dbg and x32dbg
- **Production Ready** - Tested and optimized for professional use

## Tool Categories

| Category | Tools |
|----------|-------|
| **Context** | `GetContext`, `GetAllRegisters`, `GetAllFlags`, `GetDebugStatus` |
| **Stepping** | `StepWithContext` (step + full context in one call) |
| **Analysis** | `Analyze` (disassemble N instructions) |
| **Memory** | `MemoryRead`, `MemoryWrite`, `GetMemoryInfo` |
| **Debug Control** | `DebugRun`, `DebugPause`, `DebugStop` |
| **Breakpoints** | `DebugSetBreakpoint`, `DebugDeleteBreakpoint` |
| **Registers** | `RegisterSet` |
| **Stack** | `StackOp` (pop/push/peek) |
| **Flags** | `FlagSet` |
| **Assembler** | `Assemble` (with optional memory write) |
| **Pattern** | `PatternFindMem` |
| **Modules** | `GetModuleList` |
| **Misc** | `MiscParseExpression`, `MiscRemoteGetProcAddress`, `ExecCommand` |

## Quick Setup

### 1. Build the Plugin

```bash
# Clone the repository
git clone https://github.com/0xOb5k-J/x64dbg-mcp.git
cd x64dbg-mcp

# Build both 32-bit and 64-bit plugins
cmake -S . -B build
cmake --build build --target all_plugins --config Release

# Or build single architecture
cmake -S . -B build -A x64 -DBUILD_BOTH_ARCHES=OFF
cmake --build build --config Release
```

### 2. Install Plugin

Copy the compiled plugin to x64dbg:
- `build/build64/Release/MCPx64dbg.dp64` → `x64dbg/release/x64/plugins/`
- `build/build32/Release/MCPx64dbg.dp32` → `x64dbg/release/x32/plugins/`

### 3. Configure Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "x64dbg": {
      "command": "python",
      "args": ["path/to/x64dbg.py"]
    }
  }
}
```

### 4. Start Debugging

1. Launch x64dbg and load a binary
2. Start Claude Desktop
3. Verify connection: Check x64dbg logs (Alt+L) for HTTP server message

## Token Efficiency

| Operation | Before (calls) | After (calls) | Savings |
|-----------|----------------|---------------|---------|
| Get CPU state | 25+ | 1 (`GetContext`) | ~96% |
| Get all registers | 17 | 1 (`GetAllRegisters`) | ~94% |
| Step + analyze | 4+ | 1 (`StepWithContext`) | ~75% |
| Memory info | 3 | 1 (`GetMemoryInfo`) | ~67% |

## Example Usage

```
"Analyze the current function and step through it"
→ Uses: GetContext, Analyze, StepWithContext

"Set a breakpoint at main and run to it"
→ Uses: MiscParseExpression, DebugSetBreakpoint, DebugRun

"Read 100 bytes from the PE header"
→ Uses: GetModuleList, MemoryRead
```

## Requirements

- x64dbg (latest version)
- Python 3.8+
- `mcp` Python package: `pip install mcp`
- Visual Studio 2019+ (for building)

## Credits

Based on [x64dbgMCP by Wasdubya](https://github.com/Wasdubya/x64dbgMCP) - Original implementation.

Optimized version with:
- Batch tool consolidation (39 → 23 tools)
- New efficient endpoints (`GetContext`, `StepWithContext`, `Analyze`, etc.)
- Removed broken/duplicate legacy tools
- Production-grade reliability

## License

MIT License
