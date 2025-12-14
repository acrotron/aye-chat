{
  description = "Aye Chat - AI-powered terminal workspace";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        ayechat = pkgs.callPackage ./ayechat.nix {};
      in
      {
        packages = {
          default = ayechat;
          ayechat = ayechat;
        };

        apps.default = {
          type = "app";
          program = "${ayechat}/bin/aye";
        };

        devShells.default = pkgs.mkShell {
          packages = [ ayechat ];
        };
      }
    );
}
