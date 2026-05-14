BIN := agentwiki-launcher
PKG := ./cmd/agentwiki-launcher
DIST := dist

.PHONY: build clean dist test vet

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
