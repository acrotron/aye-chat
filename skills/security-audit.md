# Security Audit Skill

## Metadata
- name: security-audit
- version: 1.0.0
- triggers: [security, audit, sec-review, vulnerability]
- description: Security-focused code review for vulnerabilities

## Instructions

Perform a security audit of the provided Python code. Focus on identifying vulnerabilities and security best practices.

### Vulnerability Categories

#### 1. Injection Attacks
- **SQL Injection**: String concatenation in SQL queries
- **Command Injection**: `os.system()`, `subprocess.run(..., shell=True)`
- **Path Traversal**: Unsanitized user input in file paths
- **Code Injection**: `eval()`, `exec()`, `compile()` with user input

#### 2. Authentication & Authorization
- Hardcoded credentials
- Weak password handling
- Missing authentication checks
- Improper session management

#### 3. Data Exposure
- Sensitive data in logs
- Secrets in source code
- Unencrypted sensitive data
- Verbose error messages exposing internals

#### 4. Insecure Dependencies
- Known vulnerable packages
- Outdated dependencies
- Unnecessary dependencies with large attack surface

#### 5. Cryptographic Issues
- Weak hashing algorithms (MD5, SHA1 for passwords)
- Hardcoded encryption keys
- Insecure random number generation
- Missing HTTPS/TLS verification

#### 6. Input Validation
- Missing input sanitization
- Type confusion vulnerabilities
- Buffer overflow risks
- Regex denial of service (ReDoS)

### Severity Levels

- 	at1f534 **CRITICAL**: Immediate exploitation risk, data breach possible
- 	at1f7e0 **HIGH**: Significant risk, should fix before deployment
- 	at1f7e1 **MEDIUM**: Moderate risk, fix in next release
- 	at1f7e2 **LOW**: Minor risk, best practice improvement
- 	at1f535 **INFO**: Security enhancement suggestion

### Output Format

```markdown
## Security Audit Report

### Summary
- **Critical**: X
- **High**: X  
- **Medium**: X
- **Low**: X

### Findings

#### 	at1f534 CRITICAL: [Vulnerability Name]
**File**: `path/to/file.py`
**Line**: XX
**CWE**: CWE-XXX (if applicable)

**Vulnerable Code**:
```python
[code snippet]
```

**Risk**: [Explain the attack vector]

**Remediation**:
```python
[fixed code]
```

---

### Security Checklist
- [ ] No hardcoded secrets
- [ ] Input validation on all user data
- [ ] Parameterized queries for database
- [ ] No shell=True in subprocess
- [ ] Proper error handling without info leak
- [ ] Dependencies are up to date
- [ ] HTTPS/TLS verification enabled
- [ ] Sensitive data encrypted at rest
```

### Common Python Security Issues

**Command Injection**:
```python
# VULNERABLE
import os
os.system(f"echo {user_input}")  # Shell injection!

# SECURE
import subprocess
subprocess.run(["echo", user_input], check=True)  # No shell
```

**Path Traversal**:
```python
# VULNERABLE
def read_file(filename):
    return open(f"/data/{filename}").read()  # ../../../etc/passwd

# SECURE
from pathlib import Path

def read_file(filename):
    base = Path("/data").resolve()
    target = (base / filename).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Path traversal detected")
    return target.read_text()
```

**SQL Injection**:
```python
# VULNERABLE
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

# SECURE
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

**Hardcoded Secrets**:
```python
# VULNERABLE
API_KEY = "sk-1234567890abcdef"  # In source code!

# SECURE
import os
API_KEY = os.environ.get("API_KEY")
```

**Insecure Deserialization**:
```python
# VULNERABLE
import pickle
data = pickle.loads(user_input)  # Arbitrary code execution!

# SECURE
import json
data = json.loads(user_input)  # Safe
```
