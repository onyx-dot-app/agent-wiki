BIN := agentwiki-launcher
PKG := ./cmd/agentwiki-launcher
DIST := dist

.PHONY: build clean dist test vet release release-linux release-appimage release-deb release-rpm release-windows

build:
	go build -o $(BIN) $(PKG)

test:
	go test ./...

vet:
	go vet ./...

clean:
	rm -rf $(BIN) $(DIST)

# Cross-compile darwin-arm64 + darwin-amd64 release binaries.
dist: clean
	mkdir -p $(DIST)
	GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o $(DIST)/$(BIN)-darwin-arm64 $(PKG)
	GOOS=darwin GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o $(DIST)/$(BIN)-darwin-amd64 $(PKG)
	@echo "Built:"
	@ls -lh $(DIST)

# macOS distributable: signed + notarized + stapled AgentWikiLauncher.app.
# See scripts/build-app.sh for env requirements + signing chain.
release:
	./scripts/build-app.sh

# Linux distributable: amd64 + arm64 tar.gz, each containing the binary
# + install.sh that copies it to ~/.local/bin and runs xdg-mime.
release-linux:
	./scripts/build-linux.sh

# Linux AppImage (amd64): single executable, double-click to run after
# chmod +x. Requires appimagetool on PATH.
release-appimage:
	./scripts/build-appimage.sh

# Linux .deb (amd64): Software Center / `dpkg -i` install. No chmod, no
# terminal. Covers Debian / Ubuntu / Mint / Pop!_OS. Requires dpkg-deb.
release-deb:
	./scripts/build-deb.sh

# Linux .rpm (amd64): GNOME Software / KDE Discover / `rpm -i` install.
# Covers Fedora / RHEL / Rocky / openSUSE. Requires rpmbuild (Linux only).
release-rpm:
	./scripts/build-rpm.sh

# Windows distributable: amd64 .exe linked with -H windowsgui. Single
# self-contained file — user downloads, double-clicks, SmartScreen,
# "Run anyway", MessageBox confirms install.
release-windows:
	./scripts/build-windows.sh
