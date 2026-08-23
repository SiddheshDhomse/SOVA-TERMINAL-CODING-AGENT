## SOVA - Terminal Coding Agent

You can now use `sova` command in PowerShell to start the agent!

### Current Status: ✅ Working
Type `sova` in any PowerShell terminal to launch the agent.

### Make it Permanent (Optional)

The alias currently works in the current session. To make it permanent across all future PowerShell sessions:

**Option 1: Automatic Setup (Recommended)**
Run this PowerShell command once:
```powershell
Set-Alias -Name sova -Value (Get-Item -Path ".\sova.bat").FullName -Scope CurrentUser -Force
```

Run from this directory: `C:\Users\i-Siddhesh.Dhomse\OneDrive - Icertis Solutions\Desktop\Terminal Agent`

**Option 2: Manual Setup**
1. Open PowerShell and run: `$PROFILE`
2. This shows your profile path (e.g., `C:\Users\{username}\Documents\PowerShell\profile.ps1`)
3. Open that file in your editor
4. Add this line:
```powershell
Set-Alias -Name sova -Value "C:\Users\i-Siddhesh.Dhomse\OneDrive - Icertis Solutions\Desktop\Terminal Agent\sova.bat"
```
5. Save and close
6. Restart PowerShell

### Quick Reference

**Start agent:**
```
sova
```

**Switch providers:**
```
/provider groq     # Groq
/provider nvidia   # Nvidia Nemotron
/provider ollama   # Ollama
```

**Other commands:**
```
/model <name>      # Override model
/new               # Reset conversation
exit               # Exit agent
Ctrl+C             # Stop current task
```

### Files

- `sova.bat` - Batch file wrapper to launch the agent
- `setup-sova-alias.ps1` - PowerShell setup script
