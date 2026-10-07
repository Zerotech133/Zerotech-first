package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.util.IdUtil;
import com.qianjiangsoft.graphcore.engine.GraphEngine;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.mapper.GraphEdgeTypeMapper;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.param.MergeEdgeParam;
import com.qianjiangsoft.graphcore.service.EdgeService;
import com.qianjiangsoft.graphcore.service.SpaceSyncService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * @author marshallwu
 * @date 2022/2/24 14:49
 */
@Service
public class EdgeServiceImpl implements EdgeService {

    @Autowired
    GraphEdgeTypeMapper edgeTypeMapper;

    @Autowired
    GraphEngine graphEngine;

    @Autowired
    SpaceSyncService spaceSyncService;

    @Override
    public Edge createEdge(EdgeType type, Vertex source, Vertex destination,
                           Map<String, Object> propertyList, Double strength, Date time, Map<String, String> cardList) {

        Edge edge = new Edge(IdUtil.simpleUUID(), source, destination,
                type, propertyList);

        edge.setStrength(strength);
        edge.setTime(time);
        edge.setCardList(cardList);

        SpaceContextHolder.get().getEdgeList().add(edge);
        return edge;
    }


    @Override
    public void createEdge(EdgeType type, String sourceId, String destinationId,
                           Map<String, Object> propertyList, Double strength, Date time, Map<String, String> cardList) {
        Space space = SpaceContextHolder.get();
        Vertex sourceVertex = space.getVertexMap().get(sourceId);
        Vertex destVertex = space.getVertexMap().get(destinationId);

        if(sourceVertex == null || destVertex == null) {
            return;
        }

        createEdge(type, sourceVertex, destVertex, propertyList, strength, time, cardList);
    }

    @Override
    public List<Edge> queryEdgeListByMergeEdge(MergeEdgeParam mergeEdge) {
        Space space = SpaceContextHolder.get();

        Set<Edge> edgeSet = space.getTypeEdgeMap().get(mergeEdge.getType());

        List<Edge> result = new ArrayList<>();

        for(Edge edge : edgeSet) {
            if(edge.getSource().equals(mergeEdge.getSource()) &&
                    edge.getDestination().equals(mergeEdge.getDestination())) {
                result.add(edge);
            }
        }
        return result;
    }

    @Override
    public void deleteEdge(String sourceId, String destinationId, EdgeType type) {
        Space space = SpaceContextHolder.get();

        Vertex sourceVertex = space.getVertexMap().get(sourceId);
        Vertex destVertex = space.getVertexMap().get(destinationId);

        space.getEdgeList().removeIf(edge -> edge.getSource().equals(sourceVertex) &&
                edge.getDestination().equals(destVertex) && edge.getType().equals(type));
    }

    @Override
    public void deleteEdgeByProperty(String propertyName, String propertyValue) {
        Space space = SpaceContextHolder.get();

        space.getEdgeList().removeIf(edge -> {
            //过滤case info
            if(edge.getPropertyList().containsKey(propertyName) &&
                    edge.getPropertyList().get(propertyName).equals(propertyValue)) {
                return true;
            }
            return false;
        });
    }
}
