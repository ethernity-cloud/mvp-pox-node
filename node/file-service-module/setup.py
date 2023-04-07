from setuptools import find_packages, setup

setup(
    name='miniolib',
    packages=find_packages(include=['miniolib']),
    version='0.0.1',
    description='File Transfer Library',
    author='Vlad Radu',
    install_requires=[],
    setup_requires=['minio'],
    tests_require=['minio==7.1.13'],
    test_suite='tests',
)
