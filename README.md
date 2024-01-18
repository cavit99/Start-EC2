# EC2 Instance Starter

This repository contains a Python script that automates the process of initializing an Amazon EC2 instance from a specified launch template. Utilizing the boto3 library, the script not only starts the instance but also establishes a secure shell (SSH) connection and configures port forwarding. This functionality is especially beneficial for deploying and managing private generative AI servers, such as Stable Diffusion, on the AWS cloud infrastructure. By leveraging port forwarding, users can interact with their cloud-based AI servers as if they were running locally.

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

## Recommended Launch Template User Data
```sh
#!/bin/bash
sudo apt-get update
cd /home/ubuntu/stable-diffusion-webui && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/kohya_ss && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/ComfyUI && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
find /home/ubuntu/stable-diffusion-webui/extensions -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
find /home/ubuntu/ComfyUI/custom_nodes -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
nohup fio --filename=/dev/nvme0n1 --rw=read --bs=1M --iodepth=32 --ioengine=libaio --direct=1 --name=volume-initialize &
```

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
