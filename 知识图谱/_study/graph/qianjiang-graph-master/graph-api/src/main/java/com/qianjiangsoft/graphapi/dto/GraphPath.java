package com.qianjiangsoft.graphapi.dto;

import com.qianjiangsoft.graphcore.model.Edge;
import com.qianjiangsoft.graphcore.model.Vertex;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/28 9:39
 */
public class GraphPath {
    Map<String, Vertex> vertexMap = new HashMap<>();
    List<Edge> edgeList = new ArrayList<>();
}
