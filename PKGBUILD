# Maintainer: Meepaw <git@github.com:Me3paw/VPN-gate-CLI.git>
pkgname=vpn-gate-client
pkgver=1.2.0
pkgrel=1
pkgdesc="A lightweight CLI and GUI for VPN Gate using NetworkManager with legacy OpenSSL support"
arch=('any')
url="https://github.com/Me3paw/VPN-gate-client"
license=('MIT')
depends=('python' 'python-requests' 'python-pyqt6' 'networkmanager' 'networkmanager-openvpn' 'hicolor-icon-theme')
makedepends=('git')
source=("${pkgname}::git+https://github.com/Me3paw/VPN-gate-client.git#tag=v${pkgver}")
sha256sums=('SKIP')

package() {
    cd "${srcdir}/${pkgname}"

    local appdir="${pkgdir}/usr/share/${pkgname}"

    # Entry points and the shared core module. They must land in the same
    # directory: each script adds its own realpath dir to sys.path.
    install -Dm755 "src/vpngate-cli.py" "${appdir}/vpngate-cli.py"
    install -Dm755 "src/vpngate-gui.py" "${appdir}/vpngate-gui.py"
    install -Dm644 "src/vpngate_core.py" "${appdir}/vpngate_core.py"

    # Bundled assets, kept at the same relative path the scripts expect.
    install -Dm644 "src/assets/requirements.txt" "${appdir}/assets/requirements.txt"
    for size in 32 64 128 256; do
        install -Dm644 "src/assets/icons/${size}.png" "${appdir}/assets/icons/${size}.png"
    done

    # Icon theme entries, used by the .desktop file and the tray icon.
    for size in 32 64 128 256; do
        install -Dm644 "src/assets/icons/${size}.png" \
            "${pkgdir}/usr/share/icons/hicolor/${size}x${size}/apps/vpngate-gui.png"
    done
    install -Dm644 "src/assets/icons/128.svg" \
        "${pkgdir}/usr/share/icons/hicolor/scalable/apps/vpngate-gui.svg"

    # Command line entry points
    install -dm755 "${pkgdir}/usr/bin"
    ln -s "/usr/share/${pkgname}/vpngate-cli.py" "${pkgdir}/usr/bin/vpngate"
    ln -s "/usr/share/${pkgname}/vpngate-gui.py" "${pkgdir}/usr/bin/vpngate-gui"

    install -Dm644 "src/assets/vpngate-gui.desktop" \
        "${pkgdir}/usr/share/applications/vpngate-gui.desktop"
    install -Dm644 "LICENSE" "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
}
