from setuptools import setup
setup(
    name='sar_integration',
    version='0.0.1',
    packages=['sar_integration'],
    install_requires=['setuptools'],
    entry_points={
        'console_scripts': [
            'sar_node = sar_integration.sar_node:main',
        ],
    },
)
