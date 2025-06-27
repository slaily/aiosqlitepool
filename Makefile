# Run unit tests
unit:
	python -m unittest discover -s tests.unit -t .

# Build package distribution
build:
	python -m build