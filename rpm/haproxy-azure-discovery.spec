%global python3_version 3.11
%global python3_pkgversion 3.11
%global pypi_name haproxy-azure-discovery
%global pkg_name haproxy_azure_discovery
%global install_dir /opt/%{pypi_name}
%global debug_package %{nil}

Name:           %{pypi_name}
Version:        0.1.0
Release:        1%{?dist}
Summary:        Azure Service Discovery Daemon for HAProxy

License:        MIT
URL:            https://github.com/gspiliotis/%{pypi_name}
Source0:        %{pypi_name}-%{version}.tar.gz

# Oracle Linux 8 — Python 3.11 from AppStream
BuildRequires:  python%{python3_pkgversion}-devel
BuildRequires:  python%{python3_pkgversion}-pip
BuildRequires:  python%{python3_pkgversion}-setuptools
BuildRequires:  python%{python3_pkgversion}-wheel
BuildRequires:  systemd-rpm-macros

Requires:       python%{python3_pkgversion}
Requires(pre):  shadow-utils
Requires(post): systemd
Requires(preun): systemd
Requires(postun): systemd

%description
A Python daemon that automatically discovers Azure VMs and Virtual Machine
Scale Set (VMSS) instances and registers them as HAProxy backends via the
Dataplane API. Supports AZ-aware server weighting, cookie-based persistence,
and per-service backend options.

This is the Azure equivalent of the AWS EC2 service discovery built into
HAProxy's Go codebase — runs as an external sidecar.

# ---------------------------------------------------------------------------
# Prep / Build / Install
# ---------------------------------------------------------------------------

%prep
%autosetup -n %{pypi_name}-%{version}

%build
# Nothing to compile — pure Python. The wheel is built during install.

%install
# Create the application virtualenv so all dependencies are self-contained
# and do not conflict with system-site packages.
mkdir -p %{buildroot}%{install_dir}
python%{python3_version} -m venv %{buildroot}%{install_dir}/venv

# Install the package and all runtime dependencies into the venv
%{buildroot}%{install_dir}/venv/bin/pip install \
    --no-cache-dir --disable-pip-version-check \
    .

# Fix shebang and paths that were baked with the buildroot prefix.
# The venv's pip writes the buildroot path into shebangs and pyvenv.cfg;
# strip it so the paths are correct on the target host.
find %{buildroot}%{install_dir}/venv -type f -name '*.py' \
    -exec sed -i 's|%{buildroot}||g' {} +
sed -i 's|%{buildroot}||g' %{buildroot}%{install_dir}/venv/pyvenv.cfg
find %{buildroot}%{install_dir}/venv/bin -type f \
    -exec sed -i 's|%{buildroot}||g' {} +

# Wrapper script in /usr/local/bin
mkdir -p %{buildroot}/usr/local/bin
cat > %{buildroot}/usr/local/bin/%{pypi_name} << 'WRAPPER'
#!/bin/bash
exec /opt/%{pypi_name}/venv/bin/%{pypi_name} "$@"
WRAPPER
chmod 0755 %{buildroot}/usr/local/bin/%{pypi_name}

# systemd unit
mkdir -p %{buildroot}%{_unitdir}
install -p -m 0644 systemd/%{pypi_name}.service %{buildroot}%{_unitdir}/%{pypi_name}.service

# Config directory and example config
mkdir -p %{buildroot}/etc/%{pypi_name}
install -p -m 0640 config.example.yaml %{buildroot}/etc/%{pypi_name}/config.yaml

# Empty environment file for secrets (mode 0600)
touch %{buildroot}/etc/%{pypi_name}/env

# ---------------------------------------------------------------------------
# Scriptlets
# ---------------------------------------------------------------------------

%pre
# Create a dedicated service account if it does not exist.
# Re-uses the haproxy user/group when available (typical sidecar deployment),
# otherwise creates its own.
getent group haproxy >/dev/null || groupadd -r haproxy
getent passwd haproxy >/dev/null || \
    useradd -r -g haproxy -d /nonexistent -s /sbin/nologin \
        -c "HAProxy Azure Discovery" haproxy
exit 0

%post
%systemd_post %{pypi_name}.service

%preun
%systemd_preun %{pypi_name}.service

%postun
%systemd_postun_with_restart %{pypi_name}.service

# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

%files
# Application
%dir %{install_dir}
%{install_dir}/venv

# Wrapper
/usr/local/bin/%{pypi_name}

# systemd
%{_unitdir}/%{pypi_name}.service

# Configuration
%dir %attr(0750, root, haproxy) /etc/%{pypi_name}
%config(noreplace) %attr(0640, root, haproxy) /etc/%{pypi_name}/config.yaml
%config(noreplace) %attr(0600, root, haproxy) /etc/%{pypi_name}/env

# Docs
%doc README.md config.example.yaml

# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------

%changelog
* Fri Feb 20 2026 George Spiliotis <g@spiliotis.net> - 0.1.0-1
- Initial RPM package
- AZ-aware server weighting and per-service backend options
- systemd service unit with security hardening
