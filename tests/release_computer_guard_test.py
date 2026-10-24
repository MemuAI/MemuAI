"""Pure text checks for the operator's explicit software-installation policy.

These tests do not construct an operator, type keys, run commands, or use a
network. Package-manager names below are inert strings passed to a regex.
"""
from __future__ import annotations

import unittest

from app.automation_runtime import _PROHIBITED_TYPED_TEXT


class ComputerInstallationTextGuard(unittest.TestCase):
    def test_explicit_package_installation_text_is_rejected(self):
        commands = (
            "winget install sample.app", "WINGET.EXE install sample.app",
            "choco install sample", "scoop install sample", "pip install sample",
            "python -m pip install sample", "pip3.exe install sample",
            "npm install sample", "npm -g install sample", "npm ci",
            "pnpm add sample", "yarn add sample", "bun i sample",
            "conda install sample", "mamba install sample", "micromamba install sample",
            "cargo install sample", "gem install sample", "brew install sample",
            "apt-get install sample", "dnf install sample", "yum install sample",
            "go install example.invalid/sample", "uv tool install sample",
            "dotnet tool install sample",
        )
        for text in commands:
            with self.subTest(text=text):
                self.assertRegex(text, _PROHIBITED_TYPED_TEXT)

    def test_windows_installer_and_installation_cmdlets_are_rejected(self):
        commands = (
            "msiexec /i sample.msi", "msiexec.exe /quiet /package sample.msi",
            "msiexec /update sample.msp", "msiexec /p sample.msp",
            "Install-Package sample", "Install-Module sample", "Install-Script sample",
            "Add-AppxPackage sample.msix", "Add-AppPackage sample.msix",
            "Add-WindowsPackage -PackagePath sample.cab",
        )
        for text in commands:
            with self.subTest(text=text):
                self.assertRegex(text, _PROHIBITED_TYPED_TEXT)

    def test_ordinary_text_and_package_information_remain_allowed(self):
        texts = (
            "Please rename the heading to Overview", "Read the installation guide",
            "The Windows installer reports progress", "PackageManager.installationStatus",
            "winget search sample", "winget list", "choco list", "scoop list",
            "pip list", "pip show sample", "python -m pip --version",
            "npm list", "npm view sample", "conda list", "cargo --version",
            "msiexec /help", "Write notes about installing software",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertNotRegex(text, _PROHIBITED_TYPED_TEXT)

    def test_existing_sensitive_text_guards_remain_in_place(self):
        for text in ("Remove-Item C:\\data -Recurse", "API key = sample-secret"):
            with self.subTest(text=text):
                self.assertRegex(text, _PROHIBITED_TYPED_TEXT)


if __name__ == "__main__":
    unittest.main()
