# EC2 Instance Starter

# AWS EC2 Instance Initialization and Secure Connection via AWS SSM

This repository contains a Python script for automating the initialization of Amazon EC2 spot instances, enabling efficient access to savings of up to 80% compared to on-demand prices. The script leverages the boto3 library to launch instances from a pre-existing Amazon Machine Image (AMI) using a user-defined launch template. It's particularly useful for setting up private generative AI inference and training Stable Diffusion via ComfyUI, A1111, as well as LLMs.

## Key Features

- **Secure Connection via AWS Systems Manager (SSM)**: The script sets up a secure connection using AWS SSM, eliminating the need for traditional SSH keys and AWS security groups. This feature enhances security and simplifies access management.

- **Port Forwarding Configuration**: The script can configure port forwarding, allowing seamless interaction with AI servers as if they were local. This feature is especially beneficial for developers needing a local-like environment for inference. Default port in the config document is 8188, which is the ComfyUI default. eg. http://127.0.0.1:8188 would reach your EC2 instance, but without needing to expose any ports to the internet in AWS Security Groups.

- **Flexible Network Design**: The script supports the creation of a highly secure instance within a private Virtual Private Cloud (VPC) without an internet gateway using AWS PrivateLink. However, this is optional, and users can choose to have their instance within a VPC that includes an internet gateway if they prefer, without needing to expose any ports to the internet.

## Getting Started

These instructions will guide you on how to use this script for starting and connecting to an EC2 instance.

## Prerequisites

Before you start, make sure you have:

- An AWS account
- [AWS CLI installed and configured](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [Install AWS System Manager plug-in](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- [A predefined EC2 launch template](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/create-launch-template.html)
- An AMI you can use to launch, which already has SSM Agent
- [Ensure your launch template will attach to the instance an IAM role with SSM permissions](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-getting-started-instance-profile.html)
- Appropriate IAM permissions


## Installation

1. Clone the repo
```sh
git clone https://github.com/cavit99/Start-EC2.git
```


2. Install the required packages
```sh
pip install -r requirements.txt
```

## Usage

Run the script
```sh
python start_ec2.py
```

Follow the prompts to select your launch template and start the instance.

## Threading Logic

This script uses Python's threading module to handle the output of the SSM port forwarding session in real-time. 

1. Main Thread: This is the primary thread that runs the main function and controls the overall flow of the script.

2. Secondary Thread: This thread is created to handle the output of the SSM port forwarding session in real-time. It runs a function called handle_output, which reads the output line by line and logs it. It also sets an event when the port forwarding session is ready.



l

## Contributing

Contributions are what make the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are greatly appreciated.

1. Fork the Project
2. Create your Feature Branch (git checkout -b feature/AmazingFeature)
3. Commit your Changes (git commit -m 'Add some AmazingFeature')
4. Push to the Branch (git push origin feature/AmazingFeature)
5. Open a Pull Request

## License

Distributed under the MIT License. See LICENSE for more information.

## Contact

Cavit Erginsoy - cavit@erginsoy.com

Project Link: https://github.com/cavit99/Start-EC2
