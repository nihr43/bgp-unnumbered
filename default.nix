{ nixpkgs ? import <nixpkgs> {  } }:

let
  pkgs = [
    nixpkgs.python311Packages.pycryptodome
    nixpkgs.python311Packages.pylxd
    nixpkgs.python311Packages.jinja2
    nixpkgs.python311Packages.ansible-runner
  ];

in
  nixpkgs.stdenv.mkDerivation {
    name = "env";
    buildInputs = pkgs;
  }
