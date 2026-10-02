# Home Assistant Add-on: Roon Server

The Roon Server add-on allows you to run a Roon Server as an add-on in Home Assistant. Roon Server is a part of a Roon audio setup and acts as a central hub in your setup, also known as a Roon Core. 

Roon is a subscription based service, this add-on is useless without the subscription. 

## Usage

You should read the Roon documentation here https://help.roonlabs.com/portal/en/kb/roon-labs-llc/getting-started to learn more about Roon if you're not familiar with the setup or the details. 

Basically you need:
 - A Roon Subscritopn
 - This Add-on
 - A roon client with the Control Software running on a device like a laptop or a smartphone
 - Roon outputs like PI's, Sonos, Airplay, Cast, etc.

Details here: https://help.roonlabs.com/portal/en/kb/articles/architecture

#### This plugin has NO userinterface (yet). You need to connect to it via a Roon Control software

## Mappings

The Add-on maps the /media folder (yes, that's the media thing you see in the GUI) and the /backup folder in the container so they are usable for Roon. 
NOTE: I try to map the back-up folder and create a back-up schedule automatically if there are no existing schedules, but please do check ALWAYS if the back-up works. Note that this is a seperate back-up method form the HA Container Snapshop back-ups you can create. The Roon Server Backups allow you to import or import the Roon Server config, including all your preferences, outputs, etc. 

## License

The Roon Server software is covered by Roon, and it's officially not allowed to redistribute it. For that reason the add-on downloads and build the Roon Server when you start the container for the first time. 

## Upgrade. 

Haven't had one yet when writing this, so not sure, but I think you should be able to just "rebuild" the add-on. This should build a new container and thus re-download the latest version of Roon Server
