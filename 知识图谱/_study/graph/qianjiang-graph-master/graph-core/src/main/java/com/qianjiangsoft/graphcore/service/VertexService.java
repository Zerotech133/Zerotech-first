package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphcore.model.Vertex;

import java.util.List;
import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/24 10:47
 */
public interface VertexService {

    Vertex createVertex(Tag tag, String id, String name, Map<String, Object> propertyList);

    void removeVertex(String id);

    Vertex findVertexById(String id);

    /**
     * 随机选择一个点
     * @param:
     * @return: com.qianjiangsoft.graphcore.model.Vertex
     * @author: marshall
     * @date: 2022/3/29
    */
    Vertex randomSelectOneVertex();

    List<Vertex> searchVertexListByName(String keyword, String tagName);

    void mergeVertex(String id1, String id2);

    void updateVertexProperty(String id, Map<String, Object> propertyList);
}
