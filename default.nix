{ nixpkgs ? import <nixpkgs> {  } }:

let
  pkgs = with nixpkgs.python311Packages; [
    pycryptodome
    pylxd
    jinja2
    ansible-runner
  ];

in
  nixpkgs.stdenv.mkDerivation {
    name = "env";
    buildInputs = pkgs;
  }
