#*************************************************************************************************************************
#import the required Python libraries. If any of the following import commands fail check the local Python environment
#and install any missing packages.
#*************************************************************************************************************************import json
import pathlib
import pika
import base64
import numpy as np
from netCDF4 import Dataset
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import io
import logging
import os.path
import sys
import json
import urllib3
import certifi
import requests
from time import sleep
from http.cookiejar import CookieJar
import urllib.request
from urllib.parse import urlencode
import getpass
import ssl
import os


ssl._create_default_https_context = ssl._create_unverified_context

#Initialize the urllib PoolManager and set the base URL for the API requests that will be sent to the GES DISC subsetting service.

# Create a urllib PoolManager instance to make requests.
http = urllib3.PoolManager(cert_reqs='CERT_REQUIRED',ca_certs=certifi.where())
# Set the URL for the GES DISC subset service endpoint
url = 'https://disc.gsfc.nasa.gov/service/subset/jsonwsp'

###################################################   RABBITMQ  CODE BEGINS    ###############################################################################
 
# establish connection with rabbitmq server 
logger = logging.getLogger('django')
connection = pika.BlockingConnection(pika.ConnectionParameters('orionRabbit'))
channel = connection.channel()
logger.info(" Connected to RBmq server")

#create/ declare queue
channel.queue_declare(queue='merra_plot_rx')

###################################################   RABBITMQ  CODE BEGINS    ###############################################################################


# definea a local general-purpose method that submits a JSON-formatted Web Services Protocol (WSP) request to the GES DISC server, checks for any errors, and then returns the response. 
# This method is created for convenience as this task will be repeated more than once.
# This method POSTs formatted JSON WSP requests to the GES DISC endpoint URL
# It is created for convenience since this task will be repeated more than once

def get_http_data(request):
    #logger.info('request',request['methodname'])
    hdrs = {'Content-Type': 'application/json',
            'Accept'      : 'application/json'}
    data = json.dumps(request)
    #logger.info('data:',data)
    #logger.info("url",url)
    r = http.request('POST', url, body=data, headers=hdrs)
    response = json.loads(r.data)   
    #logger.info('response:',response['type'])
    # Check for errors
    if response['type'] == 'jsonwsp/fault' :
        logger.info('API Error: faulty %s request' % response['methodname'])
        sys.exit(1)
    return response

# The following method constructs the and returns the Subsetted records

def constructSubsetData(minLatitude,maxLatitude, minLongitude, maxLongitude, date):

    # Define the parameters for the data subset
    # product = 'M2I3NPASM_V5.12.4' 
    # # varNames =['T', 'RH', 'O3']
    # varNames =['T']
    # minlon = -180
    # maxlon = 180
    # minlat = -90
    # maxlat = -45
    # begTime = '1980-01-01'
    # endTime = '1980-01-01'
    # begHour = '00:00'
    # endHour = '00:00'


    # Define the parameters for the data subset

    product = 'M2I3NPASM_V5.12.4' 
    varNames =['T']
    minlat = int(minLatitude)
    minlon = int(minLongitude)
    maxlat = int(maxLatitude)
    maxlon = int(maxLongitude)
    begTime = date
    endTime = date
    begHour = '00:00'
    endHour = '00:00'

    #logger.info("Beg time:",begTime)
    # Subset only the mandatory pressure levels (units are hPa)
    # 1000 925 850 700 500 400 300 250 200 150 100 70 50 30 20 10 7 5 3 2 1 
    dimName = 'lev'
    dimVals = [1,4,7,13,17,19,21,22,23,24,25,26,27,29,30,31,32,33,35,36,37]
    # Construct the list of dimension name:value pairs to specify the desired subset
    dimSlice = []
    for i in range(len(dimVals)):
        dimSlice.append({'dimensionId': dimName, 'dimensionValue': dimVals[i]})
    
    #logger.info('dimSlice:',dimSlice)

    # Construct JSON WSP request for API method: subset
    subset_request = {
        'methodname': 'subset',
        'type': 'jsonwsp/request',
        'version': '1.0',
        'args': {
            'role'  : 'subset',
            'start' : begTime,
            'end'   : begTime,
            'box'   : [minlon, minlat, maxlon, maxlat],
            'crop'  : True, 
            'data': [{'datasetId': product,
                    'variable' : varNames[0],
                    'slice': dimSlice
                    }]
        }
    }



    #logger.info("subset request:",subset_request['args']['box'])
    # Submit the subset request to the GES DISC Server
    response = get_http_data(subset_request)
    # Report the JobID and initial status
    myJobId = response['result']['jobId']
    #logger.info('Job ID: '+myJobId)
    #logger.info('Job status: '+response['result']['Status'])




    # Construct JSON WSP request for API method: GetStatus
    status_request = {
        'methodname': 'GetStatus',
        'version': '1.0',
        'type': 'jsonwsp/request',
        'args': {'jobId': myJobId}
    }

    # Check on the job status after a brief nap
    while response['result']['Status'] in ['Accepted', 'Running']:
        sleep(5)
        response = get_http_data(status_request)
        status  = response['result']['Status']
        percent = response['result']['PercentCompleted']
        #logger.info('Job status: %s (%d%c complete)' % (status,percent,'%'))
    if response['result']['Status'] == 'Succeeded' :
        logger.info('Job Finished:  %s' % response['result']['message'])
    else : 
        logger.info('Job Failed: %s' % response['fault']['code'])
        sys.exit(1)



    # Construct JSON WSP request for API method: GetResult
    batchsize = 20
    results_request = {
        'methodname': 'GetResult',
        'version': '1.0',
        'type': 'jsonwsp/request',
        'args': {
            'jobId': myJobId,
            'count': batchsize,
            'startIndex': 0
        }
    }

    # Retrieve the results in JSON in multiple batches 
    # Initialize variables, then submit the first GetResults request
    # Add the results from this batch to the list and increment the count
    results = []
    count = 0 
    #logger.info("result request:",results_request)
    response = get_http_data(results_request) 
    count = count + response['result']['itemsPerPage']
    results.extend(response['result']['items']) 

    # Increment the startIndex and keep asking for more results until we have them all
    total = response['result']['totalResults']
    while count < total :
        results_request['args']['startIndex'] += batchsize 
        response = get_http_data(results_request) 
        count = count + response['result']['itemsPerPage']
        results.extend(response['result']['items'])
        
    # Check on the bookkeeping
    logger.info('Retrieved %d out of %d expected items' % (len(results), total))



    # Sort the results into documents and URLs

    docs = []
    urls = []
    for item in results :
        try:
            # print('{} Item:'.format(item))
            if item['start'] and item['end'] : urls.append(item) 
            
        except:
            docs.append(item)

    # Print out the documentation links, but do not download them
    # print('\nDocumentation:')
    # for item in docs : print(item['label']+': '+item['link'])

    print('Iam here')  
    return urls


# Login and Download merra data
def downloadMerraData(username,password,minLatitude,maxLatitude, minLongitude, maxLongitude, date):
    
    # Earthdata Login
    if not username:
        username = 'teamorion2022'
    if not password:
        password = 'AdsSpring2022'

    # Create a password manager to deal with the 401 response that is returned from
    password_manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_manager.add_password(None, "https://urs.earthdata.nasa.gov", username, password)

    # Create a cookie jar for storing cookies. This is used to store and return the session cookie #given to use by the data server
    cookie_jar = CookieJar()
        
    # Install all the handlers.
    opener = urllib.request.build_opener (urllib.request.HTTPBasicAuthHandler (password_manager),urllib.request.HTTPCookieProcessor (cookie_jar))
    urllib.request.install_opener(opener)
        
    # Open a request for the data, and download files
    #logger.info('\n HTTP_services output:')
    urls=constructSubsetData(minLatitude,maxLatitude, minLongitude, maxLongitude, date)
    # logger.info(urls['link'])
    print("This is url stuff: {}".format(urls))
    # print('I am  also here 1')  

    url = urls[0]['link'] 
    #logger.info('URL : {}'.format(URL))
    DataRequest = urllib.request.Request(url)
    DataResponse = urllib.request.urlopen(DataRequest)
    DataBody = DataResponse.read()
    # print("This is dataBody: {}".format(DataBody))
    print('I am  also here 2')  


    """response = {}
    response['fileName'] = urls[0]['label']
    response['dataBody'] = DataBody"""

    response = {}
    # dirName='test'
    # if not os.path.isdir(dirName):
    #     print('The directory is not present. Creating a new one..')
    #     # print('Current Working Directory:{} '.format(os.getcwd()))
    #     os.mkdir(dirName)
    #     print('Current Working Directory after directory change :{} '.format( os.getcwd()))


    # else:
    #     print('The directory is present.')
    # Save file to working directory
    # print("File details: {}".format(urls))
    try:
        file_name = urls[0]['label']
        file_ = open(file_name, 'wb')
        file_.write(DataBody)
        file_.close()
        print (file_name, " is downloaded")
    except requests.exceptions.HTTPError as e:
        print('Exception occured : :{}'.format(e))

    print('Downloading is done and find the downloaded files in your current working directory')
    # logger.info("response from download:",response['fileName'])
    # logger.info("type of response:",type(response))
    response['fileName'] =file_name
    # response['data'] = DataBody
    return response

#unpack the data in message and process the message and return the output
def process_req(request):
    
    json_data = json.loads(request)
    #logger.info(json_data)
    username = json_data['username']
    password = json_data['password']
    minLatitude = json_data['minLatitude']
    maxLatitude = json_data['maxLatitude']
    minLongitude = json_data['minLongitude']
    maxLongitude = json_data['maxLongitude']
    date = json_data['merraDate']
    urls=downloadMerraData(username,password,minLatitude,maxLatitude, minLongitude, maxLongitude, date)
    # logger.info("type of urls:",type(urls))
    #logger.info("urls:",urls['fileName'])
    return urls

# plot service function 
def plotsService(body):
    b64 = []
    # logger.info("type of body:",type(body))
    print("Filename:{}".format(body))
    # print("Path exists:{}".format(body))
    #logger.info(json_data)
    file_name = body['fileName']
    print("Path exists:{}".format(os.path.exists(file_name)))

    #logger.info(file_name, " is downloaded")
    

    print('Downloading is done and find the downloaded files in your current working directory')
    #Read in NetCDF4 file (add a directory path if necessary):
    # print("This is the path:{}".format(os.getcwd()+"/"+file_name))
    # print("Files in current directory: {}".format(os.listdir(os.getcwd())))
    # print("Files in root directory: {}".format(os.listdir("/")))
    # print("Filename in str {}".format(str(file_name)))
    # path2 = os.PathLike()
    # print("This is path: {}".format(path2))
    data = Dataset(file_name, mode='r')
    # data = body['data']
    # Run the following line below to print MERRA-2 metadata. This line will print attribute and variable information. From the 'variables(dimensions)' list, choose which variable(s) to read in below.
    #logger.info(data)

    # Read in the 'T2M' 2-meter air temperature variable:
    lons = data.variables['lon'][:]
    lats = data.variables['lat'][:]
    T2M = data.variables['T'][:,:,:]

    # If using MERRA-2 data with multiple time indices in the file, the following line will extract only the first time index.
    # Note: Changing T2M[0,:,:] to T2M[10,:,:] will subset to the 11th time index.

    T2M = T2M[0,0,:]
    #logger.info(T2M)
    # Plot the data using matplotlib and cartopy

    # Set the figure size, projection, and extent
    fig = plt.figure(figsize=(8,4))
    ax = plt.axes(projection=ccrs.Robinson())
    ax.set_global()
    ax.coastlines(resolution="110m",linewidth=1)
    ax.gridlines(linestyle='--',color='black')

    # Set contour levels, then draw the plot and a colorbar
    clevs = np.arange(230,311,5)
    plt.contourf(lons, lats, T2M, clevs, transform=ccrs.PlateCarree(),cmap=plt.cm.jet)
    plt.title('MERRA-2 Air Temperature at 2m, January 2010', size=14)
    cb = plt.colorbar(ax=ax, orientation="vertical", pad=0.02, aspect=16, shrink=0.8)
    cb.set_label('K',size=12,rotation=0,labelpad=15)
    cb.ax.tick_params(labelsize=10)

    # Save the plot as a PNG image

    fig.savefig('MERRA2_t2m.png', format='png', dpi=360)

    flike = io.BytesIO()
    plt.savefig(flike)
    #logger.info(b64)
    b64.append(base64.b64encode(flike.getvalue()).decode())
    return b64

   
###################################################   RABBITMQ  CODE     ###############################################################################

#callback function for the queue 
def on_request(ch, method, props, body):
    #logger.info(" [.] Received this data %s", body) 

    response = process_req(body)
    print("Response:{} ".format(response['fileName']))
    finalprocess=plotsService(response)

    ch.basic_publish(exchange='', routing_key=props.reply_to, properties=pika.BasicProperties(correlation_id = props.correlation_id), body=json.dumps(finalprocess))
    ch.basic_ack(delivery_tag=method.delivery_tag)

# We might want to run more than one server process. 
# In order to spread the load equally over multiple servers we need to set the prefetch_count setting.
channel.basic_qos(prefetch_count=1)
                
# We declare a callback "on_request" for basic_consume, the core of the RPC server. It's executed when the request is received.
channel.basic_consume(queue='merra_plot_rx', on_message_callback=on_request)

print(" [x] Awaiting RPC requests")
channel.start_consuming()
channel.close()


###################################################   RABBITMQ  CODE ENDS    ###############################################################################





