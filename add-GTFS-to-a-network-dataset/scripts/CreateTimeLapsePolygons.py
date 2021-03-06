import datetime
import arcpy
arcpy.CheckOutExtension("Network")
arcpy.env.overwriteOutput = True

class CustomError(Exception):
    pass

try:

    #Check out the Network Analyst extension license
    if arcpy.CheckExtension("Network") == "Available":
        arcpy.CheckOutExtension("Network")
    else:
        arcpy.AddError("You must have a Network Analyst license to use this tool.")
        raise CustomError


    # ----- Get and process inputs -----

    # Service Area from the map that is ready to solve with all the desired settings
    # (except time of day - we'll adjust that in this script)
    input_network_analyst_layer = arcpy.GetParameter(0)
    desc = arcpy.Describe(input_network_analyst_layer)
    if desc.dataType != "NALayer" or desc.solverName != "Service Area Solver":
        arcpy.AddError("Input layer must be a Service Area layer.")
        raise CustomError
    
    # Output feature class of SA polygons which can be used to make a time lapse
    # End result will have a time field indicating which time of day the polygon is for
    output_feature_class = arcpy.GetParameterAsText(1)

    # Start and end day and time
    start_day_input = arcpy.GetParameterAsText(2)
    end_day_input = arcpy.GetParameterAsText(4)
    start_time_input = arcpy.GetParameterAsText(3)
    end_time_input = arcpy.GetParameterAsText(5)
    
    # For an explanation of special ArcMap generic weekday dates, see the time_of_day parameter
    # description in the Make Service Area Layer tool documentation
    # http://desktop.arcgis.com/en/arcmap/latest/tools/network-analyst-toolbox/make-service-area-layer.htm
    days = {
        "Monday": datetime.datetime(1900, 1, 1),
        "Tuesday": datetime.datetime(1900, 1, 2),
        "Wednesday": datetime.datetime(1900, 1, 3),
        "Thursday": datetime.datetime(1900, 1, 4),
        "Friday": datetime.datetime(1900, 1, 5),
        "Saturday": datetime.datetime(1900, 1, 6),
        "Sunday": datetime.datetime(1899, 12, 31)}
    
    # Lower end of time window (HH:MM in 24-hour time)
    generic_weekday = False
    if start_day_input in days: #Generic weekday
        generic_weekday = True
        start_day = days[start_day_input]
    else: #Specific date
        start_day = datetime.datetime.strptime(start_day_input, '%Y%m%d')
    start_time_dt = datetime.datetime.strptime(start_time_input, "%H:%M")
    start_time = datetime.datetime(start_day.year, start_day.month, start_day.day, start_time_dt.hour, start_time_dt.minute)

    # Upper end of time window (HH:MM in 24-hour time)
    # End time is inclusive.  An analysis will be run using the end time.
    if end_day_input in days: #Generic weekday
        if not generic_weekday:
            # The tool UI validation should prevent them from encountering this problem.
            arcpy.AddError("Your Start Day is a specific date, but your End Day is a generic weekday. \
Please use either a specific date or a generic weekday for both Start Date and End Date.")
            raise CustomError
        end_day = days[end_day_input]
        if start_day != end_day:
            # We can't interpret what the user intends if they choose two different generic weekdays,
            # and the solver won't be happy if the start day is after the end day, even if we add a \
            # week to the end day. So just don't support this case. If they want to solve across \
            # multiple days, they should use specific dates.
            # The tool UI validation should prevent them from encountering this problem.
            arcpy.AddError("If using a generic weekday, the Start Day and End Day must be the same.")
            raise CustomError

    else: #Specific date
        if generic_weekday:
            arcpy.AddError("Your Start Day is a generic weekday, but your End Day is a specific date. \
Please use either a specific date or a generic weekday for both Start Date and End Date.")
            raise CustomError
        end_day = datetime.datetime.strptime(end_day_input, '%Y%m%d')
    end_time_dt = datetime.datetime.strptime(end_time_input, "%H:%M")
    end_time = datetime.datetime(end_day.year, end_day.month, end_day.day, end_time_dt.hour, end_time_dt.minute)

    if start_time == end_time:
        arcpy.AddError("Start and end date and time are the same.")
        raise CustomError
    elif end_time < start_time:
        arcpy.AddError("End time is earlier than start time.")
        raise CustomError

    # How much to increment the time in each solve, in minutes
    increment_input = arcpy.GetParameter(6)
    increment = datetime.timedelta(minutes=increment_input)
    timelist = [] # Actual list of times to use for the analysis.
    t = start_time
    while t <= end_time:
        timelist.append(t)
        t += increment

    
    # ----- Add a TimeOfDay field to SA Polygons -----

    # Grab the polygons sublayer, which we will export after each solve.
    sublayer_names = arcpy.na.GetNAClassNames(input_network_analyst_layer) # To ensure compatibility with localized software
    polygons_subLayer = arcpy.mapping.ListLayers(input_network_analyst_layer, sublayer_names["SAPolygons"])[0]

    time_field = "TimeOfDay"

    # Clean up any pre-existing fields with this name (unlikely case)
    poly_fields = arcpy.ListFields(polygons_subLayer, time_field)
    if poly_fields:
        for f in poly_fields:
            if f.name == time_field and f.type != "Date":
                arcpy.AddWarning("Your Service Area layer's Polygons sublayer contained a field called TimeOfDay \
of a type other than Date.  This field will be deleted and replaced with a field of type Date used for the output \
of this tool.")
                arcpy.management.DeleteField(polygons_subLayer, time_field)

    # Add the TimeOfDay field to the Polygons sublayer.  If it already exists, this will do nothing.
    arcpy.na.AddFieldToAnalysisLayer(input_network_analyst_layer, sublayer_names["SAPolygons"], time_field, "DATE")


    # ----- Solve NA layer in a loop for each time of day -----

    # Grab the solver properties object from the NA layer so we can set the time of day
    solverProps = arcpy.na.GetSolverProperties(input_network_analyst_layer)

    # Solve for each time of day and save output
    arcpy.AddMessage("Solving Service Area at time...")
    for t in timelist:
        arcpy.AddMessage(str(t))
        
        # Switch the time of day
        solverProps.timeOfDay = t
        
        # Solve the Service Area
        arcpy.na.Solve(input_network_analyst_layer)
        
        # Calculate the TimeOfDay field
        expression = '"' + str(t) + '"' # Unclear why a DATE field requires a string expression, but it does.
        arcpy.management.CalculateField(polygons_subLayer, time_field, expression, "PYTHON_9.3")
        
        #Append the polygons to the output feature class. If this was the first
        #solve, create the feature class.
        if not arcpy.Exists(output_feature_class):
            arcpy.management.CopyFeatures(polygons_subLayer, output_feature_class)
        else:
            arcpy.management.Append(polygons_subLayer, output_feature_class)

except CustomError:
    pass
except:
    raise
finally:
    arcpy.CheckInExtension("Network")