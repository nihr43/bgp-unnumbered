.PHONY: lint

lint:
	black testnet
	flake8 testnet --ignore E501
	find . -name '*.yml' | xargs yamllint
	find . -name '*.yaml' | xargs yamllint
