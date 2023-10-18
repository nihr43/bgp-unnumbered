.PHONY: lint

lint:
	black testnet
	flake8 testnet --ignore E501
	yamllint -c yamllint.cfg .
