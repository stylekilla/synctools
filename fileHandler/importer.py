import os
import pydicom as dicom
import numpy as np
from synctools.fileHandler.image import image2d
from natsort import natsorted
from synctools.tools.opencl import gpu as gpuInterface
from synctools.math import wcs2wcs
import h5py
import logging

np.set_printoptions(formatter={'float': lambda x: "{0:0.2f}".format(x)})

'''
The importer class takes DICOM/HDF5 images and turns them into a
	class (image2d or image3d) for plotting in QsWidgets.QPlot().
	This is where we disconnect the DICOM information and take
	only what the internals of SyncMRT requires to operate. Maybe
	in the future such integrations could just see the use of
	DICOM throughout but then things would have to be re-written
	to understand DICOM. This is just currently my own interface.
Think of this class as the interface to QPlot. As such it should
	probably be packaged with it.
'''

class sync_dx:
	def __init__(self,dataset):
		# Read in hdf5 image arrays.
		file = h5py.File(dataset[0],'r')
		# Load the images in.
		for i in range(file.attrs['NumberOfImages']):
			if i == 0: self.image = [image2d()]
			else: self.image.append(image2d())
			self.image[i].pixelArray = file[str(i)][:]
			# Extract the extent information, should be available in image.
			self.image[i].extent = file[str(i)].attrs['extent']
			# Patient isocenter (typically the beam isocenter).
			self.image[i].patientIsocenter = file[str(i)].attrs['isocenter']
			# Import image view.
			# self.image[i].view = file[str(i)].attrs['view']
			# self.image[i].axis = file[str(i)].attrs['axis']
			self.image[i].view = {
					'title':'AP',
					'xLabel':'LR',
					'yLabel':'SI',
				}
			self.image[i].orientation = [1,2,0]

def checkDicomModality(dataset,modality):
	# Start with empty list of files.
	files = {}
	for i in range(len(dataset)):
		# Read the file in.
		testFile = dicom.dcmread(dataset[i])
		if testFile.Modality == modality:
			# Save in dict where the key is the slice position.
			files[int(testFile.SliceLocation)] = dataset[i]
		else:
			pass

	# Sort the files based on slice location.
	sortedFiles = []
	for key in sorted(files.keys()):
		sortedFiles.append(files[key])

	# Return the sorted file list.
	return sortedFiles

class dicom_ct:
	def __init__(self,dataset,gpu):
		self.fp = os.path.dirname(dataset[0])
		# Are we reading in a CT DICOM file?
		dataset = checkDicomModality(dataset,'CT')
		ref = dicom.dcmread(dataset[0])
		# Get CT shape.
		shape = np.array([int(ref.Columns), int(ref.Rows), len(dataset)])
		# Initialize image with array of zeros.
		# self.pixelArray = np.zeros(shape, dtype=np.int16)
		self.pixelArray = np.zeros(shape, dtype=np.int32)
		# Read array in one slice at a time.
		for fn in dataset:
			slice = dicom.dcmread(fn)
			self.pixelArray[:,:,dataset.index(fn)] = slice.pixel_array
			# self.pixelArray[:,:,shape[2]-dataset.index(fn)-1] = slice.pixel_array
			# Should send signal of import status here.
			# pct = dataset.index(fn)/len(dataset)
			# progress.emit(pct)
		# Rescale the Hounsfield Units.
		self.pixelArray = (self.pixelArray*ref.RescaleSlope) + ref.RescaleIntercept

		# '''
		# Map the DICOM CS (RCS) to the python CS (WCS):
		# '''
		# Get current CT orientation.
		# Machine coordinates defined here:
		# http://dicom.nema.org/medical/Dicom/2016c/output/chtml/part03/sect_C.8.8.25.6.html
		dcmAxes =  np.array(list(map(float,ref.ImageOrientationPatient)))
		x = dcmAxes[:3]
		y = dcmAxes[3:6]
		z = np.cross(x,y)
		self.orientation = np.vstack((x,y,z))
		self.RCS = np.vstack((x,y,z))
		z1 = list(map(float,ref.ImagePositionPatient))[2]
		z2 = list(map(float,dicom.dcmread(dataset[-1]).ImagePositionPatient))[2]
		spacingBetweenSlices = (z2-z1)/len(dataset)
		# Get vars for transform.
		self.pixelSize = np.append(np.array(list(map(float,ref.PixelSpacing))),spacingBetweenSlices)
		leftTop = np.array(list(map(float,ref.ImagePositionPatient)))
		# Calculate Extent.
		print("Calc CT Extent:")
		# ncs,newArraySize,oldPixelSize,leftTop=None,centrePosition=None
		self.extent = calculateExtent(self.RCS,self.pixelArray.shape,self.pixelSize,leftTop=leftTop,updatePixelSize=False)
		self.RCS_LEFTTOP = np.array([self.extent[0],self.extent[3],self.extent[4]])
		print("---------------")
		# Load array onto GPU for future reference.
		gpu.loadData(self.pixelArray,self.extent)

		# Create a 2d image list for plotting.
		self.image = [image2d(),image2d()]
		# Flatten the 3d image to the two 2d images.
		self.image[0].pixelArray = np.sum(self.pixelArray,axis=2)
		self.image[0].extent = np.array([ self.extent[0], self.extent[1], self.extent[2], self.extent[3] ])
		self.image[0].view = calculate2DViewLabels(ref.PatientPosition,self.orientation,axis=2)
		self.image[1].pixelArray = np.sum(self.pixelArray,axis=1)
		self.image[1].extent = np.array([ self.extent[4], self.extent[5], self.extent[2], self.extent[3] ])
		self.image[1].view = calculate2DViewLabels(ref.PatientPosition,self.orientation,axis=1)

		# Save and write fp and ds.
		np.save(self.fp+'/dicom_ct.npy',self.pixelArray)
		self.ds = [self.fp+'/dicom_ct.npy']
		self.fp = os.path.dirname(self.fp)

class beamClass:
	def __init__(self):
		self.image = None
		self.mask = None
		self.maskThickness = None
		self.gantry = None
		self.patientSupport = None
		self.collimator = None
		self.pitch = None
		self.roll = None
		self.isocenter = None
		self.bcs = None
		self._rcs2bcs = None

class dicom_rtplan:
	def __init__(self,dataset,rcs,rcsLeftTop,ctArrayShape,ctArrayPixelSize,gpuContext):
		# Firstly, read in DICOM rtplan file.
		ref = dicom.dcmread(dataset[0])
		# Set file path.
		self.fp = os.path.dirname(dataset[0])
		# Construct an object array of the amount of beams to be delivered.
		self.beam = np.empty(ref.FractionGroupSequence[0].NumberOfBeams,dtype=object)
		self.isocenter = np.array(list(map(float,ref.BeamSequence[0].ControlPointSequence[0].IsocenterPosition)))

		# Extract confromal mask data.
		# for i in range(len(self.beam)):
		for i in range(1):
			self.beam[i] = beamClass()
			# If a block is specified for the MLC then get it.
			if ref.BeamSequence[0].NumberOfBlocks > 0:
				self.beam[i].mask = ref.BeamSequence[i].BlockSequence[0].BlockData
				self.beam[i].maskThickness = ref.BeamSequence[i].BlockSequence[0].BlockThickness
			# Get the jaws position for backup.
			# Get the machine positions.
			self.beam[i].gantry = float(ref.BeamSequence[i].ControlPointSequence[0].GantryAngle)
			self.beam[i].patientSupport = float(ref.BeamSequence[i].ControlPointSequence[0].PatientSupportAngle)
			self.beam[i].collimator = float(ref.BeamSequence[i].ControlPointSequence[0].BeamLimitingDeviceAngle)
			self.beam[i].pitch = float(ref.BeamSequence[i].ControlPointSequence[0].TableTopPitchAngle)
			self.beam[i].roll = float(ref.BeamSequence[i].ControlPointSequence[0].TableTopRollAngle)
			# Take RCS (patient) and begin modifying it's position.
			# Rotate it into the view of the collimator.
			temp_cs = rotate_cs(rcs,[self.beam[i].pitch],[0])
			temp_cs = rotate_cs(temp_cs,[self.beam[i].roll],[1])
			temp_cs = rotate_cs(temp_cs,[self.beam[i].patientSupport],[2])
			self.beam[i].bcs = rotate_cs(temp_cs,[self.beam[i].gantry,self.beam[i].collimator],[1,2])

			# Solve the transform that takes the RCS into the BCS (Beam Coordinate System).
			self.beam[i]._rcs2bcs = wcs2wcs(rcs,self.beam[i].bcs)
			# Rotate the dataset.
			# pixelArray = gpuContext.copy()
			# self.beam[i]._rcs2bcs = np.identity(3)
			pixelArray = gpuContext.rotate(self.beam[i]._rcs2bcs)
			# Calculate the extent.
			print("CALC RT EXTENT:")
			# extent = calculateExtent(self.beam[i]._rcs2bcs,rcsLeftTop,pixelArray.shape,ctArrayPixelSize)
			ctArrayCentre = rcsLeftTop + (ctArrayPixelSize*(np.array(ctArrayShape)/2))
			extent = calculateExtent(self.beam[i]._rcs2bcs,pixelArray.shape,ctArrayPixelSize,centrePosition=ctArrayCentre)
			print("---------------")
			# Create images.
			self.beam[i].image = [image2d(),image2d()]
			# Flatten the 3d image to the two 2d images.
			self.beam[i].image[0].pixelArray = np.sum(pixelArray,axis=1)
			self.beam[i].image[0].extent = np.array([extent[0],extent[1],extent[4],extent[5]])
			self.beam[i].image[0].view = { 'xLabel': 'x', 'yLabel': 'y', 'title':'No Title' }
			# self.image[0].extent = np.array([ self.extent[0], self.extent[1], self.extent[2], self.extent[3] ])
			# self.image[0].view = calculate2DViewLabels(ref.PatientPosition,self.orientation,axis=2)
			self.beam[i].image[1].pixelArray = np.sum(pixelArray,axis=2)
			self.beam[i].image[1].extent = np.array([extent[3],extent[2],extent[4],extent[5]])
			self.beam[i].image[1].view = { 'xLabel': 'x', 'yLabel': 'y', 'title':'No Title' }
			# self.image[1].extent = np.array([ self.extent[4], self.extent[5], self.extent[2], self.extent[3] ])
			# self.image[1].view = calculate2DViewLabels(ref.PatientPosition,self.orientation,axis=1)

def rotate_cs(cs,theta,axis):
	# Put angles into radians.
	rotations = []
	for i in range(len(theta)):
		t = np.deg2rad(theta[i])
		if axis[i] == 0: r = np.array([[1,0,0],[0,np.cos(t),-np.sin(t)],[0,np.sin(t),np.cos(t)]])
		elif axis[i] == 1: r = np.array([[np.cos(t),0,np.sin(t)],[0,1,0],[-np.sin(t),0,np.cos(t)]])
		elif axis[i] == 2: r = np.array([[np.cos(t),-np.sin(t),0],[np.sin(t),np.cos(t),0],[0,0,1]])
		rotations.append(r)

	# Calculate out the combined rotations.
	m = np.identity(3)
	for i in range(len(rotations)):
		m = rotations[len(rotations)-1-i]@m

	rotated_cs = np.zeros(cs.shape)
	# Rotate coordinate system.
	for i in range(3):
		rotated_cs[i] = np.transpose(m@np.transpose(cs[i]))
	# Return the rotated cs.+self.pixelSize*np.sign()
	return rotated_cs

def calculateExtent(ncs,newArraySize,oldPixelSize,leftTop=None,centrePosition=None,updatePixelSize=True):
	# oldPixelSize, centrePosition and leftTop must be in mm.
	# newArraySize must be in np.shape format.
	# ncs must be a 3x3 transform (row0: x, row1: y, row2: z).
	# X and Y mappings onto the coordinate system.
	x = ncs[0]
	y = ncs[1]
	z = ncs[2]
	# Calculate new pixelsize.
	newPixelSize = ncs@np.transpose(oldPixelSize)
	# Ensure newarraysize is np array.
	newArraySize = np.array(newArraySize)
	if (leftTop is None) & (centrePosition is None):
		logging.critical('Cannot calculate extent when no reference point is given. Either leftTop or centrePosition must be assigned a vector.')
		return
	else:
		if leftTop is None:
			# No leftTop value, calculate it from centrePosition.
			newCentrePosition = ncs@np.transpose(centrePosition)
			leftTop = newCentrePosition - newPixelSize*(newArraySize/2)
		else:
			# Left top is specified. 
			pass
	# Calculate the transform to take pixel location into dicom location.
	# M = np.array([
	# 	[x[0]*pixelSize[0], y[0]*pixelSize[1],  z[0]*pixelSize[2], leftTop[0]],
	# 	[x[1]*pixelSize[0], y[1]*pixelSize[1],  z[1]*pixelSize[2], leftTop[1]],
	# 	[x[2]*pixelSize[0], y[2]*pixelSize[1],  z[2]*pixelSize[2], leftTop[2]],
	# 	[0, 0, 0, 1]
	# ])

	# Find centre of array (mm).
	# centre = M@np.array(imageSize/2)
	# print('centre: ',centre)
	# Get new pixel size (mm).
	# newPixelSize = M@np.transpose(np.array([1,1,1,0]))
	# Calculate extent (lr,bt,fb). Swapped y1 and y2 as the axis goes in the other direction for mpl extent... stupid.
	x1 = leftTop[0]
	y2 = leftTop[1]
	z1 = leftTop[2]
	x2 = leftTop[0] + newArraySize[0]*newPixelSize[0]
	y1 = leftTop[1] + newArraySize[1]*newPixelSize[1]
	z2 = leftTop[2] + newArraySize[2]*newPixelSize[2]
	extent = np.array([x1,x2,y1,y2,z1,z2])
	# print('extent: ',extent)
	return extent

def calculate2DViewLabels(patientPosition,rcs,axis=0):
	'''
		patientPosition can be Head/Feet First Supine/Prone.
		rcs is the dicomFile.ImageOrientation (the cosine mappings of the X and Y axes).

		Labels are calculated as degrees from axis:
		-90  -60  -45  -30   0   +30  +45  +60  +90
		 |----|////|////|----|----|////|////|----|
		"B"      "AB"       "A"       "AC"      "C"

		rcs stored as 3x3 matrix where first row is X axis projected on i,j,k, then Y, then Z.
		[ Xi Xj Xk ]   [ 1 0 0 ]
		[ Yi Yj Yk ] = [ 0 1 0 ]
		[ Zi Zj Zk ]   [ 0 0 1 ]
	'''
	# Set the labels for the patient position.
	rcsLabels = None 
	if patientPosition == 'HFS': rcsLabels = np.array(['R','L','P','A','I','S'])
	elif patientPosition == 'HFP': rcsLabels = np.array(['R','L','A','P','I','S'])
	elif patientPosition == 'FFS': rcsLabels = np.array(['L','R','P','A','S','I'])
	elif patientPosition == 'FFP': rcsLabels = np.array(['L','R','A','P','S','I'])
	# Specify vectors for axes.
	wcs_x = rcs[0]
	wcs_y = rcs[1]
	wcs_z = rcs[2]

	# Check to see if wcs_x = wcs_y (i.e they are at 45 deg)
	if np.array_equal(rcs[0],rcs[1]):
		# It is preferred to keep them on their same axes.
		wcs_x = np.sign(wcs_x[0])*[1,0,0]
		wcs_y = np.sign(wcs_y[0])*[0,1,0]

	wcsLabels = {}
	wcsLabels['title'] = '?'
	wcsLabels['xLabel'] = '?'
	wcsLabels['yLabel'] = '?'

	# Find which axis each is maximised on.
	wcs_axes = [wcs_x, wcs_y, wcs_z]
	wcs_max = [np.argmax(np.absolute(wcs_x)), np.argmax(np.absolute(wcs_y)), np.argmax(np.absolute(wcs_z))]
	wcs_max = wcs_max * np.sign([np.amax(wcs_x), np.amax(wcs_y), np.amax(wcs_z)])

	for idx, val in enumerate(wcs_max):
		# Get direction and axis.
		wcs_axis = int(np.absolute(val))
		# Find corresponding labels.
		labels = rcsLabels[wcs_axis*2:wcs_axis*2+2]
		# Order labels.
		if np.sign(val) == -1: labels = np.flip(labels)
		# Special case for axis 1 because we really flatten it in -1 (reverse direction).
		if (axis == 1) & (idx == 0): labels = np.flip(labels)
		# Assign to label.
		if axis == 2:		
			if idx == 0: wcsLabels['xLabel'] = ''.join(labels)
			elif idx == 1: wcsLabels['yLabel'] = ''.join(labels)
			elif idx == 2: wcsLabels['title'] = ''.join(labels)
		elif axis == 1:		
			if idx == 2: wcsLabels['xLabel'] = ''.join(labels)
			elif idx == 1: wcsLabels['yLabel'] = ''.join(labels)
			elif idx == 0: wcsLabels['title'] = ''.join(labels)
		elif axis == 0:		
			if idx == 0: wcsLabels['xLabel'] = ''.join(labels)
			elif idx == 2: wcsLabels['yLabel'] = ''.join(labels)
			elif idx == 1: wcsLabels['title'] = ''.join(labels)

	return wcsLabels

def roundInt(value):
	sign = np.sign(value)
	value = np.absolute(value)
	if (value > (2**-0.5)): 
		# value = 1
		if sign == 1.0:
			value = 0
		else:
			value = 1
	else:
		# value = 0
		value = -1
	return value