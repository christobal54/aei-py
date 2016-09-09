#!/usr/bin/python
##
## reads and applies standard processing parameters to planet rgb imagery
##
####################
##
import aei as aei
import numpy as np
import gdal as gdal
import osr as osr
import shutil
import json
import glob
import cv2

# define global parameters
class default_params:
    def __init__(self):
        
        # set base file parameters
        self.imageFile = None
        self.metadataFile = None
        self.geoinfoFile = None
        self.inputPath = None
        self.basename = None
        
        # set some metadata parameters
        self.snr = None
        self.exposureTime = None
        self.altitude = None
        self.offNadir = None
        self.solarZenith = None
        self.solarAzimuth = None
        self.localTime = None
        self.cloudCover = None

# parse command line arguments
class parse_args:
    def __init__(self, arglist, params):
        
        # we'll be parsing the command line arguments passed
        #  via arglist, updating the default parameters,
        #  then returning the params list 
        
        # exit if no arguments passed
        if len(arglist) == 1:
            usage(exit=True)
    
        # read arguments from command line
        i = 1
        
        while i < len(arglist):
            arg = arglist[i]
        
            # check input flag    
            if arg.lower() == '-i':
                i += 1
                arg = arglist[i]
                
                # if input is a path, find the input files
                if aei.params.os.path.isdir(arg):
                    params.inputPath = arg
                    search = glob.glob(arg + aei.params.pathsep + "*")
                    
                    # reject if no files found
                    if len(search) == 0:
                        usage()
                        print("[ ERROR ]: no files found in input directory")
                        sys.exit(1)
                        
                    for file in search:
                        if ".tif" in file:
                            params.imageFile = file
                        if "_metadata.json" in file:
                            params.metadataFile = file
                        if "_geoinfo.json" in file:
                            params.geoinfoFile = file
                
                # if input is a .tif file            
                else:
                    dirname = aei.params.os.path.dirname(arg)
                    params.inputPath = dirname
                    params.imageFile = arg
                    
                    # find json files
                    loc = arg.find("_analytic.tif")
                    
                    if loc == -1:
                        usage()
                        print("[ ERROR ]: input tif file does not end with _analytic.tif")
                        print("[ ERROR ]: input tif file: %s" % arg)
                        sys.exit(1)
                        
                    else:
                        params.metadataFile = arg[:loc] + "_metadata.json"
                        params.geoinfoFile = arg[:loc] + "_geoinfo.json"

# read the metadata json file        
def readMetadata(params):
    
    # read the json metadata info into memory
    jdict = json.load(params.metadataFile)
    j = jdict[u'properties']
    
    # extract useful metadata info
    self.exposureTime = j[u'camera'][u'exposure_time']
    self.cloudCover = j[u'cloud_cover'][u'estimated']
    self.snr = j[u'snr']
    self.altitude = j[u'sat'][u'alt']
    self.offNadir = j[u'sat'][u'off_nadir']
    self.solarZenith = 90-j[u'sun'][u'altitude']
    self.solarAzimuth = j[u'sun'][u'azimuth']
    self.localTime = j[u'sun'][u'local_time_of_day']

# make sure files exist    
def checkInputs(params):
    if not aei.fn.checkFile(params.imageFile):
        usage()
        aei.fn.checkFile(params.imageFile, False)
        sys.exit(1)
        
    if not aei.fn.checkFile(params.metadataFile):
        usage()
        aei.fn.checkFile(params.metadataFile, False)
        sys.exit(1)
           
    if not aei.fn.checkFile(params.geoinfoFile):
        usage()
        aei.fn.checkFile(params.geoinfoFile, False)
        sys.exit(1)
        
# set up function to calculate simple ratios
def simpleRatio(band1, band2):
    one = np.ones(1, dtype=np.float32)[0]
    gd = np.where(band2 == 0)
    return (band1[gd[0],gd[1]] * one) / (band2[gd[0],gd[1]] * one)

# set up function to calculate normalized ratios    
def normalizedRatio(band1, band2):
    one = np.ones(1, dtype=np.float32)[0]
    band1 *= one
    band2 *= one
    gd = np.where((band1 + band2) == 0)
    return (band1[gd[0],gd[1]] - band2[gd[0],gd[1]]) / (band1[gd[0],gd[1]] + band2[gd[0],gd[1]])

# set up function to calculate distance from the edge of good data
def distanceFromEdge(array, gdalRef):
    """
    calculates the distance from the edge of an image.
     
    expects the input array to be the alpha band (a uint16 2-dimension array)
    """
    # find the good data values in the alpha band
    gd = np.where(array == array.max())
    
    # and find the converse bad data to mask later
    bd = np.where(array != array.max())
    
    # create a byte array and set good data to 255
    byte = np.zeros(array.shape, dtype = np.uint8)
    byte[gd[0], gd[1]] = 255
    
    # create a kernel to close up small chunks of no-data
    kernel = np.ones([50, 50], np.uint8)
    closing = cv2.morphologyEx(byte, cv2.MORPH_CLOSE, kernel)
    
    # detect edges on cleaned up data
    edges = cv2.Canny(closing, 0, 255)
    
    # set up a temp output file
    newFile = gdal.GetDriverByName("GTiff").Create("temp_edge.tif", array.shape[1], 
        array.shape[0], 1, gdal.GDT_Byte, options=["COMPRESS=LZW"])
    
    # set up projection info
    geo = gdalRef.GetGeoTransform()
    prj = gdalRef.GetProjection()
    newFile.SetGeoTransform(geo)
    newFile.SetProjection(prj)
    
    # write the data
    band = newFile.GetRasterBand(1)
    band.WriteArray(edges)
    band.FlushCache()
    
    # clear from memory
    band = None
    newFile = None
        
    # run gdal_proximity to get distance from edge
    aei.cmd.gdal_proximity("temp_edge.tif", "temp_proximity.tif", 
        of="GTiff", distunits="GEO")
        
    # read the output into memory
    dfeRef = gdal.Open("temp_proximity.tif")
    dfe = dfeRef.ReadAsArray()
    
    # clear the reference from memory
    dfeRef = None
    
    # delete the files 
    aei.params.os.remove("temp_edge.tif")
    aei.params.os.remove("temp_proximity.tif")
    
    # mask the bad data
    dfe[bd[0],bd[1]] = 0.
    
    # return the final array
    return dfe
    
def usage(exit=False):
    """
    describes the aeiplanet.py procedure in case of incorrect parameter calls
    
    syntax: usage(exit=False)
    """

    print(
        """
$ aeiplanet.py -i input_files
    
    output band list is:
    1. Red  2. Green  3. Blue  4. Alpha  5. Brightness normalized (BN) Red
    6. BN Green 7. BN Blue 8. Brightness scalar 9. Distance from edge (geographic units)
    10. Simple Ratio (SR) red-green, 11. SR green-blue 12. SR red-blue
    13. Normalized difference (ND) red-green 14. ND green-blue 15. ND red-blue
    Exposure Time, Off-Nadir, SNR, Solar Zenith, Solar Azimuth, Sensor Altitude
        """)
        

def main ():
    """
    the main program for aeiplanet.py

    syntax: main()
    """
    import gdal
    import numpy as np
    import cv2

    # first, read the default parameters to get the processing object
    params = default_params()
    
    # then parse the arguments passed via command line
    args = aei.params.sys.argv
    parse_args(args, params)
    
    # check inputs
    checkInputs(params)
    
    # parse the json data
    readMetadata(params)
    
    # report starting
    print('[ STATUS ]: Running aeiplanet.py')
    print('[ STATUS ]: Input file: %s' % params.imageFile)
    
    # read the rgb image
    print('[ STATUS ]: Reading data')
    gdalRef = gdal.Open(params.imageFile)
    rgba = gdalRef.ReadAsArray()
    
    # transform the data into components 
    alpha = rgba[3]
    rgbf = rgba[0:3] * 1.0
    
    # perform the brightness normalization
    print('[ STATUS ]: Brightness normalizing')
    bn, bnScalar = aei.functions.bn(rgbf, returnScalar = True, axis = 0)
    
    # find the distance from the edge
    print('[ STATUS ]: Finding distance from image edge')
    dfe = distanceFromEdge(alpha, gdalRef)
    
    # run the simple ratios
    print('[ STATUS ]: Caculating simple ratios')
    sr_rg = simpleRatio(rgb[0], rgb[1])
    sr_gb = simpleRatio(rgb[1], rgb[2])
    sr_rb = simpleRatio(rgb[0], rgb[2])
    
    # run the normalized ratios
    print('[ STATUS ]: Calculating normalized ratios')
    nd_rg = normalized_ratio(rgb[0], rgb[1])
    nd_gb = normalized_ratio(rgb[1], rgb[2])
    nd_rb = normalized_ratio(rgb[0], rgb[2])
    
    # add bands from the metadata
    print('[ STATUS ]: Adding metadata bands')
    md_exposure = np.zeros([alpha.shape[0], alpha.shape[1]], dtype=np.float32) + params.exposureTime
    md_offNadir = np.zeros([alpha.shape[0], alpha.shape[1]], dtype=np.float32) + params.offNadir
    md_solarZen = np.zeros([alpha.shape[0], alpha.shape[1]], dtype=np.float32) + params.solarZenith
    md_solarAzi = np.zeros([alpha.shape[0], alpha.shape[1]], dtype=np.float32) + params.solarAzimuth
    md_altitude = np.zeros([alpha.shape[0], alpha.shape[1]], dtype=np.float32) + params.altitude
    md_snr = np.zeros([alpha.shape[0], alpha.shape[1]], dtype=np.float32) + params.snr
    
    # create the final band stack
    print('[ STATUS ]: Creating the output stack')
    outputArray = nump.concatenate([rgba, bn, bnScalar, dfe, sr_rg, sr_gb, sr_rb,
        nd_rg, nd_gb, nd_rb, md_exposure, md_offNadir, md_solarZen, md_solarAzi, 
        md_altitude, md_snr])
        
    # set nodata for all bands
    nd = -9999.
    bad = np.where(alpha != alpha.max())
    outputArray[:, bad[0], bad[1]] = nd
      
    # create the output file
    print('[ STATUS ]: Writing the output file: %s' % params.outputFile)
    outRef = gdal.GetDriverByName("GTiff").Create(params.outputFile, gdalRef.RasterXSize, 
        gdalRef.RasterYSize, outputArray.shape[0], options=["COMPRESS=LZW"])
      
    # add georeferencing info
    outRef.SetGeoTransform(gdalRef.GetGeoTransform())
    outRef.SetProjection(gdalRef.GetProjection())
    
    # loop through each band and write the data
    for i in range(outputArray.shape[0]):
        band = outRef.GetRasterBand(i + 1)
        band.SetNoDataValue(nd)
        band.WriteArray(outputArray[i])
        band.FlushCache()
    
    # add overviews
    print('[ STATUS ]: Building overviews')
    outRef.BuildOverviews("NEAREST", [2, 4, 8, 16, 32, 64])
        
    # kill your references and clean up
    outRef = None
    gdalRef = None
    
    # final report
    print('[ STATUS ]: aeiplanet.py complete!')
    print('[ STATUS ]: Please see output file: %s' % params.outputFile)
    
    # high fives