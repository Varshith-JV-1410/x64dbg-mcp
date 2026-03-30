import sys
import os
import inspect
import json
import time
from typing import Any, Dict, List, Callable
import requests

from mcp.server.fastmcp import FastMCP

DEFAULT_X64DBG_SERVER = "http://127.0.0.1:8888/"

def _resolve_server_url_from_args_env() -> str:
    env_url = os.getenv("X64DBG_URL")
    if env_url and env_url.startswith("http"):
        return env_url
    if len(sys.argv) > 1 and isinstance(sys.argv[1], str) and sys.argv[1].startswith("http"):
        return sys.argv[1]
    return DEFAULT_X64DBG_SERVER

x64dbg_server_url = _resolve_server_url_from_args_env()

def set_x64dbg_server_url(url: str) -> None:
    global x64dbg_server_url
    if url and url.startswith("http"):
        x64dbg_server_url = url

mcp = FastMCP("x64dbg-mcp")

def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


def _parse_addr(addr: str) -> int | None:
    try:
        text = str(addr).strip()
        if not text:
            return None
        if text.lower().startswith("0x"):
            return int(text[2:], 16)
        # x64dbg endpoints in this project interpret bare addresses as hex.
        return int(text, 16)
    except Exception:
        return None


def _parse_size(size: str) -> int | None:
    text = str(size).strip()
    if not text:
        return None

    try:
        if text.lower().startswith("0x"):
            return int(text[2:], 16)
        return int(text, 10)
    except Exception:
        try:
            return int(text, 0)
        except Exception:
            return None


def _normalize_addr(addr: str) -> str:
    parsed = _parse_addr(addr)
    return f"0x{parsed:x}" if parsed is not None else str(addr)


def _normalize_size(size: str) -> str:
    parsed = _parse_size(size)
    return str(parsed) if parsed is not None else str(size)


def _is_hex_blob(text: str) -> bool:
    cleaned = "".join(text.split()).lower()
    if not cleaned or len(cleaned) % 2 != 0:
        return False
    return all(c in "0123456789abcdef" for c in cleaned)


def _parse_maybe_hex_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text[2:], 16)
        return int(text, 16)
    except Exception:
        try:
            return int(text, 10)
        except Exception:
            return None


def _repair_invalid_json_escapes(raw: str) -> str:
    # Fixes invalid backslash escapes from manual JSON assembly (e.g. Windows paths).
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(raw):
        ch = raw[i]

        if ch == '"' and (i == 0 or raw[i - 1] != "\\"):
            in_string = not in_string
            out.append(ch)
            i += 1
            continue

        if in_string and ch == "\\":
            if i + 1 >= len(raw):
                out.append("\\\\")
                i += 1
                continue

            nxt = raw[i + 1]
            if nxt in ('"', "\\", "/", "b", "f", "n", "r", "t"):
                out.append("\\")
                i += 1
                continue

            if nxt == "u" and i + 5 < len(raw):
                out.append("\\")
                i += 1
                continue

            # Invalid escape in JSON string, keep the slash as a literal backslash.
            out.append("\\\\")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _try_parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        pass

    repaired = _repair_invalid_json_escapes(raw)
    if repaired != raw:
        try:
            return json.loads(repaired)
        except Exception:
            pass

    return None


def _parse_pattern(pattern: str) -> list[int | None]:
    text = pattern.strip()
    if not text:
        return []

    tokens = text.split()

    # Support compact forms like "4D5A".
    if len(tokens) == 1:
        compact = tokens[0]
        if len(compact) % 2 == 0 and all(c in "0123456789abcdefABCDEF?*" for c in compact):
            tokens = [compact[i:i + 2] for i in range(0, len(compact), 2)]

    parsed: list[int | None] = []
    for token in tokens:
        t = token.strip()
        if t in ("?", "??", "*"):
            parsed.append(None)
            continue
        try:
            parsed.append(int(t, 16))
        except Exception:
            return []

    return parsed


def _find_pattern_offset(data: bytes, needle: list[int | None]) -> int | None:
    n = len(needle)
    if n == 0 or len(data) < n:
        return None

    for i in range(0, len(data) - n + 1):
        for j, b in enumerate(needle):
            if b is not None and data[i + j] != b:
                break
        else:
            return i

    return None


def safe_get(endpoint: str, params: dict = None, timeout: int = 15):
    """
    Perform a GET request with optional query parameters.
    Returns parsed JSON if possible, otherwise text content
    """
    if params is None:
        params = {}

    url = f"{x64dbg_server_url}{endpoint}"

    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.encoding = 'utf-8'
        if response.ok:
            # Try to parse as JSON first
            try:
                return response.json()
            except ValueError:
                return response.text.strip()
        else:
            return f"Error {response.status_code}: {response.text.strip()}"
    except Exception as e:
        return f"Request failed: {str(e)}"

def safe_post(endpoint: str, data: dict | str, timeout: int = 15):
    """
    Perform a POST request with data.
    Returns parsed JSON if possible, otherwise text content
    """
    try:
        url = f"{x64dbg_server_url}{endpoint}"
        if isinstance(data, dict):
            response = requests.post(url, data=data, timeout=timeout)
        else:
            response = requests.post(url, data=data.encode("utf-8"), timeout=timeout)
        
        response.encoding = 'utf-8'
        
        if response.ok:
            # Try to parse as JSON first
            try:
                return response.json()
            except ValueError:
                return response.text.strip()
        else:
            return f"Error {response.status_code}: {response.text.strip()}"
    except Exception as e:
        return f"Request failed: {str(e)}"

# =============================================================================
# TOOL REGISTRY INTROSPECTION (for CLI/Claude tool-use)
# =============================================================================

def _get_mcp_tools_registry() -> Dict[str, Callable[..., Any]]:
    """
    Build a registry of available MCP-exposed tool callables in this module.
    Heuristic: exported callables starting with an uppercase letter.
    """
    registry: Dict[str, Callable[..., Any]] = {}
    for name, obj in globals().items():
        if not name or not name[0].isupper():
            continue
        if callable(obj):
            try:
                # Validate signature to ensure it's a plain function
                inspect.signature(obj)
                registry[name] = obj
            except (TypeError, ValueError):
                pass
    return registry

def _describe_tool(name: str, func: Callable[..., Any]) -> Dict[str, Any]:
    sig = inspect.signature(func)
    params = []
    for p in sig.parameters.values():
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            # Skip non-JSON friendly params in schema
            continue
        params.append({
            "name": p.name,
            "required": p.default is inspect._empty,
            "type": "string" if p.annotation in (str, inspect._empty) else ("boolean" if p.annotation is bool else ("integer" if p.annotation is int else "string"))
        })
    return {
        "name": name,
        "description": (func.__doc__ or "").strip(),
        "params": params
    }

def _list_tools_description() -> List[Dict[str, Any]]:
    reg = _get_mcp_tools_registry()
    return [_describe_tool(n, f) for n, f in sorted(reg.items(), key=lambda x: x[0].lower())]

def _invoke_tool_by_name(name: str, args: Dict[str, Any]) -> Any:
    reg = _get_mcp_tools_registry()
    if name not in reg:
        return {"error": f"Unknown tool: {name}"}
    func = reg[name]
    try:
        # Prefer keyword invocation; convert all values to strings unless bool/int expected
        sig = inspect.signature(func)
        bound_kwargs: Dict[str, Any] = {}
        for p in sig.parameters.values():
            if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY):
                continue
            if p.name in args:
                value = args[p.name]
                # Simple coercions for common types
                if p.annotation is bool and isinstance(value, str):
                    value = value.lower() in ("1", "true", "yes", "on")
                elif p.annotation is int and isinstance(value, str):
                    try:
                        value = int(value, 0)
                    except Exception:
                        try:
                            value = int(value)
                        except Exception:
                            pass
                bound_kwargs[p.name] = value
        return func(**bound_kwargs)
    except Exception as e:
        return {"error": str(e)}

# =============================================================================
# Claude block normalization helpers
# =============================================================================

def _block_to_dict(block: Any) -> Dict[str, Any]:
    try:
        # Newer anthropic SDK objects are Pydantic models
        if hasattr(block, "model_dump") and callable(getattr(block, "model_dump")):
            return block.model_dump()
    except Exception:
        pass
    if isinstance(block, dict):
        return block
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", None),
            "name": getattr(block, "name", None),
            "input": getattr(block, "input", {}) or {},
        }
    # Fallback generic representation
    return {"type": str(btype or "unknown"), "raw": str(block)}

# =============================================================================
# UNIFIED COMMAND EXECUTION
# =============================================================================

@mcp.tool()
def ExecCommand(cmd: str) -> dict:
    """
    Execute a command in x64dbg and return its output
    
    Parameters:
        cmd: Command to execute
    
    Returns:
        Dictionary with:
        - success: Whether command executed successfully
        - command: The command that was executed
        - message: Status message (output goes to x64dbg Log window)
    """
    # Prefer POST body to avoid query-string encoding issues for commands with spaces.
    result = safe_post("ExecCommand", cmd, timeout=15)

    if isinstance(result, dict):
        return result

    if isinstance(result, str):
        parsed = _try_parse_json(result)
        if isinstance(parsed, dict):
            return parsed

    # Fallback to legacy GET behavior for compatibility.
    fallback = safe_get("ExecCommand", {"cmd": cmd}, timeout=15)
    if isinstance(fallback, dict):
        return fallback

    if isinstance(fallback, str):
        parsed = _try_parse_json(fallback)
        if isinstance(parsed, dict):
            return parsed

    return {"success": False, "command": cmd, "message": _to_text(fallback)}

# =============================================================================
# DEBUGGING STATUS
# =============================================================================

@mcp.tool()
def GetDebugStatus() -> dict:
    """
    Get debugger status in a single call (replaces IsDebugActive + IsDebugging)
    
    Returns:
        Dictionary with:
        - debugging: True if x64dbg has a process loaded
        - running: True if process is currently running (not paused)
    """
    debugging = safe_get("Is_Debugging")
    active = safe_get("IsDebugActive")
    
    is_debugging = False
    is_running = False
    
    if isinstance(debugging, dict) and "isDebugging" in debugging:
        is_debugging = debugging["isDebugging"] is True
    elif isinstance(debugging, str):
        try:
            parsed = json.loads(debugging)
            is_debugging = parsed.get("isDebugging", False) is True
        except: pass
    
    if isinstance(active, dict) and "isRunning" in active:
        is_running = active["isRunning"] is True
    elif isinstance(active, str):
        try:
            parsed = json.loads(active)
            is_running = parsed.get("isRunning", False) is True
        except: pass
    
    return {"debugging": is_debugging, "running": is_running}
# =============================================================================
# OPTIMIZED BATCH TOOLS (Token-efficient combined operations)
# =============================================================================

@mcp.tool()
def GetAllRegisters() -> dict:
    """
    Get all general-purpose registers in a single call (replaces 16+ individual RegisterGet calls)
    
    Returns:
        Dictionary with all register values (rax-r15 on x64, eax-eip on x32)
    """
    result = safe_get("GetAllRegisters")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except:
            return {"error": "Failed to parse registers", "raw": result}
    return {"error": "Unexpected response"}

@mcp.tool()
def GetAllFlags() -> dict:
    """
    Get all CPU flags in a single call (replaces 9 individual FlagGet calls)
    
    Returns:
        Dictionary with all flag values: zf, cf, of, sf, pf, af, df, tf, if
    """
    result = safe_get("GetAllFlags")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except:
            return {"error": "Failed to parse flags", "raw": result}
    return {"error": "Unexpected response"}

@mcp.tool()
def GetContext() -> dict:
    """
    Get complete CPU context in ONE call: all registers + flags + current instruction
    This replaces 25+ individual tool calls with a single efficient call.
    
    Returns:
        Dictionary containing:
        - regs: all register values
        - flags: all CPU flags (zf, cf, of, sf, pf)
        - instr: current instruction {addr, asm, size}
    """
    result = safe_get("GetContext")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except:
            return {"error": "Failed to parse context", "raw": result}
    return {"error": "Unexpected response"}

@mcp.tool()
def StepWithContext(step_type: str = "in") -> dict:
    """
    Execute a step and return complete CPU context in ONE call.
    Replaces: StepIn/StepOver/StepOut + GetAllRegisters + GetAllFlags + DisasmGetInstructionAtRIP
    
    Parameters:
        step_type: "in" (step into), "over" (step over), or "out" (step out). Default: "in"
    
    Returns:
        Dictionary containing:
        - step: the step type executed
        - regs: all register values after step
        - flags: CPU flags after step
        - instr: current instruction after step {addr, asm, size}
    """
    result = safe_get("StepWithContext", {"type": step_type})
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except:
            return {"error": "Failed to parse step result", "raw": result}
    return {"error": "Unexpected response"}

@mcp.tool()
def GetMemoryInfo(addr: str) -> dict:
    """
    Get comprehensive memory info for an address in ONE call.
    Replaces: MemoryIsValidPtr + MemoryBase + MemoryGetProtect
    
    Parameters:
        addr: Memory address (hex format, e.g. "0x7ff6ba690000")
    
    Returns:
        Dictionary containing:
        - addr: the queried address
        - valid: whether pointer is valid
        - base: module/region base address
        - size: region size
        - protect: memory protection flags
    """
    result = safe_get("GetMemoryInfo", {"addr": addr})
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except:
            return {"error": "Failed to parse memory info", "raw": result}
    return {"error": "Unexpected response"}

@mcp.tool()
def Analyze(addr: str = "", count: int = 10) -> dict:
    """
    Disassemble and analyze code at an address. If no address given, uses current RIP.
    More efficient than multiple DisasmGetInstruction calls.
    
    Parameters:
        addr: Start address (hex). Empty = current instruction pointer
        count: Number of instructions to disassemble (default: 10, max: 100)
    
    Returns:
        Dictionary containing:
        - base: module base address
        - instructions: list of {a: address, i: instruction, s: size}
    """
    params = {"count": str(count)}
    if addr:
        params["addr"] = addr
    result = safe_get("Analyze", params)
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except:
            return {"error": "Failed to parse analysis", "raw": result}
    return {"error": "Unexpected response"}

# =============================================================================
# REGISTER API
# =============================================================================

# REMOVED: Use GetAllRegisters() or GetContext() instead
# def RegisterGet - covered by batch tools

@mcp.tool()
def RegisterSet(register: str, value: str) -> str:
    """
    Set register value using Script API
    
    Parameters:
        register: Register name (e.g. "eax", "rax", "rip")
        value: Value to set (in hex format, e.g. "0x1000")
    
    Returns:
        Status message
    """
    return safe_get("Register/Set", {"register": register, "value": value})

# =============================================================================
# MEMORY API (Enhanced)
# =============================================================================

@mcp.tool()
def MemoryRead(addr: str, size: str) -> str:
    """
    Read memory using enhanced Script API
    
    Parameters:
        addr: Memory address (in hex format, e.g. "0x1000")
        size: Number of bytes to read
    
    Returns:
        Hexadecimal string representing the memory contents
    """
    addr_norm = _normalize_addr(addr)
    size_norm = _normalize_size(size)

    requested_size = _parse_size(size_norm)
    start_addr = _parse_addr(addr_norm)
    if requested_size is None or requested_size <= 0:
        return "Error: invalid read size"
    if start_addr is None:
        return "Error: invalid address"

    result = safe_get("Memory/Read", {"addr": addr_norm, "size": size_norm})
    text = _to_text(result)
    if _is_hex_blob(text):
        return "".join(text.split())

    legacy = safe_get("MemRead", {"addr": addr_norm, "size": size_norm})
    legacy_text = _to_text(legacy)
    if _is_hex_blob(legacy_text):
        return "".join(legacy_text.split())

    # If the target is running, pause and retry once because reads often fail while executing.
    status = GetDebugStatus()
    if isinstance(status, dict) and status.get("running") is True:
        try:
            DebugPause()
            time.sleep(0.1)
        except Exception:
            pass

        retry = safe_get("MemRead", {"addr": addr_norm, "size": size_norm})
        retry_text = _to_text(retry)
        if _is_hex_blob(retry_text):
            return "".join(retry_text.split())

    # Chunked fallback within region bounds to avoid cross-region failures.
    max_size = requested_size
    mem_info = GetMemoryInfo(addr_norm)
    if isinstance(mem_info, dict):
        region_base = _parse_maybe_hex_int(mem_info.get("base"))
        region_size = _parse_maybe_hex_int(mem_info.get("size"))
        if region_base is not None and region_size is not None and start_addr >= region_base:
            region_end = region_base + region_size
            if region_end > start_addr:
                max_size = min(max_size, region_end - start_addr)

    if max_size <= 0:
        return text if text.lower().startswith("error") else legacy_text

    offset = 0
    chunk_size = 0x1000
    chunks: list[str] = []
    while offset < max_size:
        this_size = min(chunk_size, max_size - offset)
        this_addr = f"0x{start_addr + offset:x}"
        piece = safe_get("MemRead", {"addr": this_addr, "size": str(this_size)})
        piece_text = _to_text(piece)
        if not _is_hex_blob(piece_text):
            break
        chunks.append("".join(piece_text.split()))
        offset += this_size

    if chunks:
        return "".join(chunks)

    # Return the most specific error message we have.
    if legacy_text.lower().startswith("error") or legacy_text.lower().startswith("request failed"):
        return legacy_text
    return text

@mcp.tool()
def MemoryWrite(addr: str, data: str) -> str:
    """
    Write memory using enhanced Script API
    
    Parameters:
        addr: Memory address (in hex format, e.g. "0x1000")
        data: Hexadecimal string representing the data to write
    
    Returns:
        Status message
    """
    return safe_get("Memory/Write", {"addr": addr, "data": data})

# REMOVED: Use GetMemoryInfo instead (batch operation)
# def MemoryIsValidPtr - covered by GetMemoryInfo
# def MemoryGetProtect - covered by GetMemoryInfo

# =============================================================================
# DEBUG API
# =============================================================================

@mcp.tool()
def DebugRun() -> str:
    """
    Resume execution of the debugged process using Script API
    
    Returns:
        Status message
    """
    result = safe_get("Debug/Run", timeout=5)
    text = _to_text(result)

    # Some x64dbg setups block this request while process continues running.
    if "timed out" in text.lower():
        fallback = ExecCommand("run")
        if isinstance(fallback, dict):
            if fallback.get("success") is True:
                return "Debug run command issued"
            return str(fallback.get("message", "Debug run command status unknown"))
        return _to_text(fallback)

    return text

@mcp.tool()
def DebugPause() -> str:
    """
    Pause execution of the debugged process using Script API
    
    Returns:
        Status message
    """
    return safe_get("Debug/Pause")

@mcp.tool()
def DebugStop() -> str:
    """
    Stop debugging using Script API
    
    Returns:
        Status message
    """
    return safe_get("Debug/Stop")

# REMOVED: Use StepWithContext instead (returns full context after step)
# def DebugStepIn - use StepWithContext(step_type="in")
# def DebugStepOver - use StepWithContext(step_type="over")
# def DebugStepOut - use StepWithContext(step_type="out")

@mcp.tool()
def DebugSetBreakpoint(addr: str) -> str:
    """
    Set breakpoint at address using Script API
    
    Parameters:
        addr: Memory address (in hex format, e.g. "0x1000")
    
    Returns:
        Status message
    """
    return safe_get("Debug/SetBreakpoint", {"addr": addr})

@mcp.tool()
def DebugDeleteBreakpoint(addr: str) -> str:
    """
    Delete breakpoint at address using Script API
    
    Parameters:
        addr: Memory address (in hex format, e.g. "0x1000")
    
    Returns:
        Status message
    """
    return safe_get("Debug/DeleteBreakpoint", {"addr": addr})

# =============================================================================
# ASSEMBLER API
# =============================================================================

@mcp.tool()
def Assemble(addr: str, instruction: str, write_to_memory: bool = False) -> dict:
    """
    Assemble instruction at address. Optionally write directly to memory.
    
    Parameters:
        addr: Memory address (in hex format, e.g. "0x1000")
        instruction: Assembly instruction (e.g. "mov eax, 1")
        write_to_memory: If True, patches memory directly. If False, returns bytes.
    
    Returns:
        Dictionary with assembly result (success, size, bytes) or status message
    """
    if write_to_memory:
        result = safe_get("Assembler/AssembleMem", {"addr": addr, "instruction": instruction})
        return {"success": "success" in str(result).lower(), "message": result}
    else:
        result = safe_get("Assembler/Assemble", {"addr": addr, "instruction": instruction})
        if isinstance(result, dict):
            return result
        elif isinstance(result, str):
            try:
                return json.loads(result)
            except:
                return {"error": "Failed to parse assembly result", "raw": result}
        return {"error": "Unexpected response format"}

# =============================================================================
# STACK API
# =============================================================================

@mcp.tool()
def StackOp(operation: str, value: str = "") -> str:
    """
    Stack operations: pop, push, or peek
    
    Parameters:
        operation: "pop", "push", or "peek"
        value: Value for push, or offset for peek (default: "0")
    
    Returns:
        Stack value in hex format
    """
    if operation == "pop":
        return safe_get("Stack/Pop")
    elif operation == "push":
        return safe_get("Stack/Push", {"value": value})
    elif operation == "peek":
        return safe_get("Stack/Peek", {"offset": value or "0"})
    else:
        return "Invalid operation. Use: pop, push, peek"

# REMOVED: Use StackOp instead
# def StackPop - use StackOp("pop")
# def StackPush - use StackOp("push", value)
# def StackPeek - use StackOp("peek", offset)

# =============================================================================
# FLAG API
# =============================================================================
# FLAG API
# =============================================================================

# REMOVED: Use GetAllFlags or GetContext instead
# def FlagGet - use GetAllFlags() or GetContext()

@mcp.tool()
def FlagSet(flag: str, value: bool) -> str:
    """
    Set CPU flag value using Script API
    
    Parameters:
        flag: Flag name (ZF, OF, CF, PF, SF, TF, AF, DF, IF)
        value: Flag value (True/False)
    
    Returns:
        Status message
    """
    return safe_get("Flag/Set", {"flag": flag, "value": "true" if value else "false"})

# =============================================================================
# PATTERN API
# =============================================================================

@mcp.tool()
def PatternFindMem(start: str, size: str, pattern: str) -> str:
    """
    Find pattern in memory using Script API
    
    Parameters:
        start: Start address (in hex format, e.g. "0x1000")
        size: Size to search
        pattern: Pattern to find (e.g. "48 8B 05 ? ? ? ?")
    
    Returns:
        Found address in hex format or error message
    """
    start_norm = _normalize_addr(start)
    size_norm = _normalize_size(size)

    result = safe_get("Pattern/FindMem", {"start": start_norm, "size": size_norm, "pattern": pattern})
    text = _to_text(result)

    if text.lower().startswith("0x"):
        return text

    start_int = _parse_addr(start_norm)
    size_int = _parse_size(size_norm)
    needle = _parse_pattern(pattern)

    if start_int is None or size_int is None or size_int <= 0 or not needle:
        return text

    mem_hex = MemoryRead(start_norm, size_norm)
    if mem_hex.lower().startswith("error") or mem_hex.lower().startswith("request failed"):
        return text

    try:
        mem = bytes.fromhex("".join(mem_hex.split()))
    except Exception:
        return text

    offset = _find_pattern_offset(mem, needle)
    if offset is None:
        return text

    return f"0x{start_int + offset:x}"

# =============================================================================
# MISC API
# =============================================================================

@mcp.tool()
def MiscParseExpression(expression: str) -> str:
    """
    Parse expression using Script API
    
    Parameters:
        expression: Expression to parse (e.g. "[esp+8]", "kernel32.GetProcAddress")
    
    Returns:
        Parsed value in hex format
    """
    return safe_get("Misc/ParseExpression", {"expression": expression})

@mcp.tool()
def MiscRemoteGetProcAddress(module: str, api: str) -> str:
    """
    Get remote procedure address using Script API
    
    Parameters:
        module: Module name (e.g. "kernel32.dll")
        api: API name (e.g. "GetProcAddress")
    
    Returns:
        Function address in hex format
    """
    return safe_get("Misc/RemoteGetProcAddress", {"module": module, "api": api})

# =============================================================================
# LEGACY/DEPRECATED FUNCTIONS (Not exposed via MCP - use optimized tools above)
# These are kept for internal/CLI compatibility but removed from MCP to reduce token usage
# Use instead: MemoryRead, MemoryWrite, DebugSetBreakpoint, DebugRun, etc.
# =============================================================================

# DEPRECATED: Use RegisterSet instead
def SetRegister(name: str, value: str) -> str:
    """[DEPRECATED] Use RegisterSet instead"""
    cmd = f"r {name}={value}"
    return ExecCommand(cmd)

# DEPRECATED: Use MemoryRead instead (identical functionality)
def MemRead(addr: str, size: str) -> str:
    """[DEPRECATED] Use MemoryRead instead"""
    return safe_get("MemRead", {"addr": addr, "size": size})

# DEPRECATED: Use MemoryWrite instead (identical functionality)
def MemWrite(addr: str, data: str) -> str:
    """[DEPRECATED] Use MemoryWrite instead"""
    return safe_get("MemWrite", {"addr": addr, "data": data})

# DEPRECATED: Use DebugSetBreakpoint instead (ExecCommand is broken)
def SetBreakpoint(addr: str) -> str:
    """[DEPRECATED] Use DebugSetBreakpoint instead"""
    return ExecCommand(f"bp {addr}")

# DEPRECATED: Use DebugDeleteBreakpoint instead
def DeleteBreakpoint(addr: str) -> str:
    """[DEPRECATED] Use DebugDeleteBreakpoint instead"""
    return ExecCommand(f"bpc {addr}")

# DEPRECATED: Use DebugRun instead
def Run() -> str:
    """[DEPRECATED] Use DebugRun instead"""
    return ExecCommand("run")

# DEPRECATED: Use DebugPause instead
def Pause() -> str:
    """[DEPRECATED] Use DebugPause instead"""
    return ExecCommand("pause")

# DEPRECATED: Use DebugStepIn or StepWithContext instead
def StepIn() -> str:
    """[DEPRECATED] Use DebugStepIn or StepWithContext instead"""
    return ExecCommand("sti")

# DEPRECATED: Use DebugStepOver or StepWithContext instead
def StepOver() -> str:
    """[DEPRECATED] Use DebugStepOver or StepWithContext instead"""
    return ExecCommand("sto")

# DEPRECATED: Use DebugStepOut or StepWithContext instead
def StepOut() -> str:
    """[DEPRECATED] Use DebugStepOut or StepWithContext(step_type='out') instead"""
    return ExecCommand("rtr")

# DEPRECATED: ExecCommand is broken, needs fix
def GetCallStack() -> list:
    """[DEPRECATED] ExecCommand-based, currently broken"""
    result = ExecCommand("k")
    return [{"info": "Call stack information requested via command", "result": result}]

# DEPRECATED: Use DisasmGetInstruction or Analyze instead
def Disassemble(addr: str) -> dict:
    """[DEPRECATED] Use DisasmGetInstruction or Analyze instead"""
    return {"addr": addr, "command_result": ExecCommand(f"dis {addr}")}

# REMOVED: Use Analyze instead (more efficient, customizable count)
# def DisasmGetInstruction - use Analyze(addr, count=1)
# def DisasmGetInstructionRange - use Analyze(addr, count=N)
# def DisasmGetInstructionAtRIP - use GetContext() or Analyze()
# def StepInWithDisasm - use StepWithContext (returns full context)


@mcp.tool()
def GetModuleList() -> list:
    """
    Get list of loaded modules in the debugged process
    
    Returns:
        List of module information: name, base, size, entry, sectionCount, path
    """
    result = safe_get("GetModuleList")
    # Handle various response formats, including malformed JSON with unescaped backslashes.
    if isinstance(result, list):
        return result

    if isinstance(result, dict):
        if "raw" in result and isinstance(result["raw"], str):
            parsed_raw = _try_parse_json(result["raw"])
            if isinstance(parsed_raw, list):
                return parsed_raw
            if isinstance(parsed_raw, dict):
                return [parsed_raw]
        return [result]

    if isinstance(result, str):
        parsed = _try_parse_json(result)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return [{"error": "Failed to parse module list", "raw": result}]

    return [{"error": "Unexpected response format"}]

# REMOVED: Use GetMemoryInfo instead (provides base, size, valid, protect in one call)
# def MemoryBase - use GetMemoryInfo(addr) which returns base, size, valid, protect

import argparse

def main_cli():
    parser = argparse.ArgumentParser(description="x64dbg MCP CLI wrapper")

    parser.add_argument("tool", help="Tool/function name (e.g. ExecCommand, RegisterGet, MemoryRead)")
    parser.add_argument("args", nargs="*", help="Arguments for the tool")
    parser.add_argument("--x64dbg-url", dest="x64dbg_url", default=os.getenv("X64DBG_URL"), help="x64dbg HTTP server URL")

    opts = parser.parse_args()

    if opts.x64dbg_url:
        set_x64dbg_server_url(opts.x64dbg_url)

    # Map CLI call → actual MCP tool function
    if opts.tool in globals():
        func = globals()[opts.tool]
        if callable(func):
            try:
                # Try to unpack args dynamically
                result = func(*opts.args)
                print(json.dumps(result, indent=2))
            except TypeError as e:
                print(f"Error calling {opts.tool}: {e}")
        else:
            print(f"{opts.tool} is not callable")
    else:
        print(f"Unknown tool: {opts.tool}")


def claude_cli():
    parser = argparse.ArgumentParser(description="Chat with Claude using x64dbg MCP tools")
    parser.add_argument("prompt", nargs=argparse.REMAINDER, help="Initial user prompt. If empty, read from stdin")
    parser.add_argument("--model", dest="model", default=os.getenv("ANTHROPIC_MODEL", "claude-3-7-sonnet-2025-06-20"), help="Claude model")
    parser.add_argument("--api-key", dest="api_key", default=os.getenv("ANTHROPIC_API_KEY"), help="Anthropic API key")
    parser.add_argument("--system", dest="system", default="You can control x64dbg via MCP tools.", help="System prompt")
    parser.add_argument("--max-steps", dest="max_steps", type=int, default=100, help="Max tool-use iterations")
    parser.add_argument("--x64dbg-url", dest="x64dbg_url", default=os.getenv("X64DBG_URL"), help="x64dbg HTTP server URL")
    parser.add_argument("--no-tools", dest="no_tools", action="store_true", help="Disable tool-use (text-only)")

    opts = parser.parse_args()

    if opts.x64dbg_url:
        set_x64dbg_server_url(opts.x64dbg_url)

    # Resolve prompt
    user_prompt = " ".join(opts.prompt).strip()
    if not user_prompt:
        user_prompt = sys.stdin.read().strip()
    if not user_prompt:
        print("No prompt provided.")
        return

    try:
        import anthropic
    except Exception as e:
        print("Anthropic SDK not installed. Run: pip install anthropic")
        print(str(e))
        return

    if not opts.api_key:
        print("Missing Anthropic API key. Set ANTHROPIC_API_KEY or pass --api-key.")
        return

    client = anthropic.Anthropic(api_key=opts.api_key)

    tools_spec: List[Dict[str, Any]] = []
    if not opts.no_tools:
        tools_spec = [
            {
                "name": "mcp_list_tools",
                "description": "List available MCP tool functions and their parameters.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "mcp_call_tool",
                "description": "Invoke an MCP tool by name with arguments.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                        "args": {"type": "object"}
                    },
                    "required": ["tool"],
                },
            },
        ]

    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": user_prompt}
    ]

    step = 0
    while True:
        step += 1
        response = client.messages.create(
            model=opts.model,
            system=opts.system,
            messages=messages,
            tools=tools_spec if not opts.no_tools else None,
            max_tokens=1024,
        )

        # Print any assistant text
        assistant_text_chunks: List[str] = []
        tool_uses: List[Dict[str, Any]] = []
        for block in response.content:
            b = _block_to_dict(block)
            if b.get("type") == "text":
                assistant_text_chunks.append(b.get("text", ""))
            elif b.get("type") == "tool_use":
                tool_uses.append(b)

        if assistant_text_chunks:
            print("\n".join(assistant_text_chunks))

        if not tool_uses or opts.no_tools:
            break

        # Prepare tool results as a new user message
        tool_result_blocks: List[Dict[str, Any]] = []
        for tu in tool_uses:
            name = tu.get("name")
            tu_id = tu.get("id")
            input_obj = tu.get("input", {}) or {}
            result: Any
            if name == "mcp_list_tools":
                result = {"tools": _list_tools_description()}
            elif name == "mcp_call_tool":
                tool_name = input_obj.get("tool")
                args = input_obj.get("args", {}) or {}
                result = _invoke_tool_by_name(tool_name, args)
            else:
                result = {"error": f"Unknown tool: {name}"}

            # Ensure serializable content (string)
            try:
                result_text = json.dumps(result)
            except Exception:
                result_text = str(result)

            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tu_id,
                "content": result_text,
            })

        # Normalize assistant content to plain dicts
        assistant_blocks = [_block_to_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": assistant_blocks})
        messages.append({"role": "user", "content": tool_result_blocks})

        if step >= opts.max_steps:
            break

if __name__ == "__main__":
    # Support multiple modes:
    #  - "serve" or "--serve": run MCP server
    #  - "claude" subcommand: run Claude Messages chat loop
    #  - default: tool invocation CLI
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--serve", "serve"):
            mcp.run()
        elif sys.argv[1] == "claude":
            # Shift off the subcommand and re-dispatch
            sys.argv.pop(1)
            claude_cli()
        else:
            main_cli()
    else:
        mcp.run()
