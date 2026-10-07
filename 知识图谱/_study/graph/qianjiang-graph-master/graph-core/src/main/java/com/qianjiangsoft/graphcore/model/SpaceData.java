package com.qianjiangsoft.graphcore.model;

import lombok.Data;

import java.util.ArrayList;
import java.util.List;

/**
 * @author marshallwu
 * @date 2022/3/17 8:53
 */
@Data
public class SpaceData {
    /** 点和边原始数据 */
    List<Vertex> vertexList = new ArrayList<>();
    List<Edge> edgeList = new ArrayList<>();
}
