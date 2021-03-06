import math
import numpy as np

'''
ASSUMPTIONS:
	1. That the orientation of the two objects are the same vertically (i.e. one cannot be upside down and the other upright.)
	2. CT is left points in mm, XR is right points in mm.
'''

# Create a class to find the transform between two WCS's.
class affineTransform:
	def __init__(self,leftCS,rightCS,rtpIsoc,userOrigin,xrIsoc):
	# def __init__(self,leftCS,rightCS,rtpIsoc):
		self.advance = True

		if type(leftCS) == type(int()):
			'''If single integer, then pass back all zeroes.'''
			self.theta = 0
			self.phi = 0
			self.gamma = 0
			self.translation = np.array([0,0,0])
			self.scale = 0

			self.advance = False

		if self.advance == True:
			pass
		else:
			return

		'''Points should come in as xyz cols and n-points rows: np.array((n,xyz))'''
		self.n = np.shape(leftCS)[0]

		# LEFT and RIGHT points should be in mm.
		self.l = leftCS
		self.r = rightCS

		# Find the centroids of the LEFT and RIGHT WCS.
		self.l_ctd = centroid(self.l)
		self.r_ctd = centroid(self.r)

		# Find the LEFT and RIGHT points in terms of their centroids (notation: LEFT Prime, RIGHT Prime)
		self.lp = np.zeros([self.n,3])
		self.rp = np.zeros([self.n,3])

		for i in range(self.n):
			self.lp[i,:] = np.subtract(self.l[i,:],self.l_ctd)
			self.rp[i,:] = np.subtract(self.r[i,:],self.r_ctd)

		# Find the quaternion matrix, N.
		self.N = quaternion(self.lp,self.rp)

		# Solve eigenvals and vec that maximises rotation.
		val, self.vec = eigensolve(self.N)

		# Extract transformation quarternion from evec.
		self.q = np.zeros((4,1))
		self.q[0] = self.vec[0][0]
		self.q[1] = self.vec[1][0]
		self.q[2] = self.vec[2][0]
		self.q[3] = self.vec[3][0]

		# Compute rotation matrix, R.
		self.R = rotationmatrix(self.q)
		self.R = np.reshape(self.R,(3,3))

		# Extract individual angles in degrees.
		self.theta, self.phi, self.gamma = extractangles(self.R,self.l,self.r)

		# The xray goal is always 0,0,0. The isoc of the coordinate system at IMBL.
		xrayIsoc = np.array([0,0,0])

		# Apply rotation and solve translation between centroids.
		r_ctd_rotated = np.dot(self.R,self.r_ctd.transpose())

		# Centroid to ptv isoc (according to the treatment plan).
		ctd2ptv = rtpIsoc - self.l_ctd

		# Move x-ray centroid to x-ray isocenter.
		ctdTranslation = xrayIsoc - r_ctd_rotated

		# Final translation is a combination of the ctd and ptv isocenter translations.
		self.translation = ctdTranslation + ctd2ptv

		# userOrigin = np.array(userOrigin)
		# rtpIsoc = np.array(rtpIsoc)
		# ptvIsoc = np.array((0,0,0)) - (userOrigin - rtpIsoc)
		# ptvIsocHam = np.array((ptvIsoc[0],-ptvIsoc[2],ptvIsoc[1]))
		# ctd2ptv = ptvIsocHam - self.l_ctd
		# xrPtv = self.r_ctd + ctd2ptv
		# translation = xrIsoc - xrPtv
		# self.translation = np.array((translation[0],-translation[1],translation[2]))

		# Extract scale.
		self.getscale()


		# print('=====================================')
		# print('ctd2ctd',ctd2ctd)
		# print('xrCtd',self.r_ctd)
		# print('ctCtd',self.l_ctd)
		# print('ctd2ptv',ctd2ptv)
		# print('ptvIsocHam',ptvIsocHam)
		# print('xrPtv',xrPtv)

	# Obtain scale factor between coordinate systems. Requires left and right points in reference to centroids.
	def getscale(self):
		D = np.zeros((self.n,1))
		S_l = np.zeros((self.n,1))

		for i in range(self.n):
			D[i,0] = np.dot(np.array([self.rp[i,:]]),np.dot(self.R,np.array([self.lp[i,:]]).T))[0][0]
			S_l[i,0] = np.linalg.norm(self.lp[i,:])**2

		self.scale = np.sum(D)/np.sum(S_l)

# Find the centroid of a set of points (pts).
def centroid(pts):
	n = np.shape(pts)[0]
	ctd = (1/n)*np.sum(pts,axis=0)
	return ctd

# Pass left and right coordinate system points in and pass out the matrix N.
def quaternion(l,r):
	# Calculate sum of products matrix, M.
	M = np.dot(l.transpose(),r)

	# Calculate xx, xy, xz, yy ... zz. 
	sxx = M[0,0]
	sxy = M[0,1]
	sxz = M[0,2]
	syx = M[1,0]
	syy = M[1,1]
	syz = M[1,2]
	szx = M[2,0]
	szy = M[2,1]
	szz = M[2,2]

	# Calculate N
	N = np.array([[sxx+syy+szz, syz-szy, szx-sxz, sxy-syx],
	[syz-szy, sxx-syy-szz, sxy+syx, szx+sxz],
	[szx-sxz, sxy+syx, -sxx+syy-szz, syz+szy],
	[sxy-syx, szx+sxz, syz+szy, -sxx-syy+szz]])

	# Return the matrix N
	return N

#  Find the eigenvector and eigenvalue for a given matrix.
def eigensolve(arr):
	#  Find the eigen vector and value of the array, arr.
	e, v = np.linalg.eig(arr)

	#  Find the maximum eigen value and it's corresponding eigen vector.
	val = np.amax(e)
	ind = np.argmax(e)
	i = np.unravel_index(ind,np.shape(e))
	vec = v[:,i]

	# Return the maximum eigenvalue and corresponding eigenvector.
	return val, vec

# Find the rotation matrix for a given eigen-solution.
def rotationmatrix(q):
	#  Calculate rotation matrix, R, based off quarternion input. This should be the eigenvector solution to N.
	R = np.array([[(q[0]**2+q[1]**2-q[2]**2-q[3]**2), 2*(q[1]*q[2]-q[0]*q[3]), 2*(q[1]*q[3]+q[0]*q[2])],
	[2*(q[2]*q[1]+q[0]*q[3]), (q[0]**2-q[1]**2+q[2]**2-q[3]**2), 2*(q[2]*q[3]-q[0]*q[1])],
	[2*(q[3]*q[1]-q[0]*q[2]), 2*(q[3]*q[2]+q[0]*q[1]), (q[0]**2-q[1]**2-q[2]**2+q[3]**2)]])

	# Order of rotation? XYZ or ZYX?

	# Return the rotation matrix, R.
	return R

# Extract individual rotations around the x, y and z axis seperately. 
def extractangles(R,l,r):
	# x -> H2
	# y -> H1
	# z -> vertical
	# Two possible angles for x.
	y = []
	# y.append(-np.arcsin(R[2][0]))
	# y.append(np.pi-np.arcsin(R[2][0]))
	y.append(np.arcsin(R[0][2]))
	y.append(np.pi-np.arcsin(R[0][2]))

	# Angle for Z
	z = []
	for i in range(len(y)):
		# z.append(-np.arctan2(R[0][1]/np.cos(y[i]),R[0][0]/np.cos(y[i])))
		z.append(np.arctan2(-R[0][1]/np.cos(y[i]),R[0][0]/np.cos(y[i])))

	# Angle for X
	x = []
	for i in range(len(y)):
		# x.append(-np.arctan2(R[1][2]/np.cos(y[i]),R[2][2]/np.cos(y[i])))
		x.append(np.arctan2(-R[1][2]/np.cos(y[i]),R[2][2]/np.cos(y[i])))

	solutions = []

	for i in range(len(y)):
		rotation = np.array([x[i],y[i],z[i]])

		# rotationVector = np.array([[np.cos(rotation[1])*np.cos(rotation[2]), 
		# 	np.cos(rotation[1])*np.sin(rotation[2]), 
		# 	-np.sin(rotation[1])],
		# 	[np.sin(rotation[0])*np.sin(rotation[1])*np.cos(rotation[2])-np.cos(rotation[0])*np.sin(rotation[2]), 
		# 	np.sin(rotation[0])*np.sin(rotation[1])*np.sin(rotation[2])+np.cos(rotation[0])*np.cos(rotation[2]), 
		# 	np.sin(rotation[0])*np.cos(rotation[1])],
		# 	[np.cos(rotation[0])*np.sin(rotation[1])*np.cos(rotation[2])+np.sin(rotation[0])*np.sin(rotation[2]), 
		# 	np.cos(rotation[0])*np.sin(rotation[1])*np.sin(rotation[2])-np.sin(rotation[0])*np.cos(rotation[2]), 
		# 	np.cos(rotation[0])*np.cos(rotation[1])]])

		rotationVector = np.array([[np.cos(rotation[1])*np.cos(rotation[2]), 
			-np.cos(rotation[1])*np.sin(rotation[2]), 
			-np.sin(rotation[1])],
			[np.sin(rotation[0])*np.sin(rotation[1])*np.cos(rotation[2])-np.cos(rotation[0])*np.sin(rotation[2]), 
			np.sin(rotation[0])*np.sin(rotation[1])*np.sin(rotation[2])+np.cos(rotation[0])*np.cos(rotation[2]), 
			-np.sin(rotation[0])*np.cos(rotation[1])],
			[np.cos(rotation[0])*np.sin(rotation[1])*np.cos(rotation[2])+np.sin(rotation[0])*np.sin(rotation[2]), 
			np.cos(rotation[0])*np.sin(rotation[1])*np.sin(rotation[2])-np.sin(rotation[0])*np.cos(rotation[2]), 
			np.cos(rotation[0])*np.cos(rotation[1])]])

		value = []
		value.append(x[i])
		value.append(y[i])
		value.append(z[i])
		error = 0
		for i in range(len(l)):
			# error += np.sum(np.square(np.absolute( (l[i].T - np.dot(R,r[i].T)) )))
			error += np.sum(np.absolute( (l[i].T - np.dot(rotationVector,r[i].T)) ))

		value.append(error)
		solutions.append(value)

	# Find minimum error.
	print('Solution List')
	print(np.rad2deg(solutions))

	errorList = []
	for i in range(len(solutions)):
		errorList.append(solutions[i][3])

	success = False
	while (success == False):
		try:
			index = np.argmin(errorList)
		except:
			print("Unable to solve for the alignment.")
			return 0,0,0

		v = np.rad2deg(y[index])
		h2 = np.rad2deg(x[index])
		h1 = np.rad2deg(z[index])

		if ((-360 < v < 360) & (-90 < h2 < 90) & (-90 < h1 < 90)):
			success = True
		else:
			try:
				del errorList[index]
				del x[index]
				del y[index]
				del z[index]
			except:
				print('Please select the points properly.')
				return 0, 0, 0

	# Angles must be applied in zyx order.
	return h2, v, h1