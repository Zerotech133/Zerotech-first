package com.qianjiangsoft.graphcore.helper;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.map.MapUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONUtil;
import com.qianjiangsoft.graphcore.constants.Constants;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.model.*;

import java.util.*;
import java.util.stream.Collectors;

/**
 * @author marshallwu
 * @date 2022/6/10 9:56
 */
public class ModelHelper {

    /**
     * 原始边计算成合并边
     * @param: edgeList
     * @return: java.util.List<com.qianjiangsoft.graphcore.model.MergeEdge>
     * @author: marshall
     * @date: 2022/6/21
    */
    public static List<MergeEdge> computeMergeEdgeList(List<Edge> edgeList){
        List<MergeEdge> mergeEdgeList = new ArrayList<>();

        //统计强度次数
        Map<String, List<Edge>> mergeEdgeListMap = edgeList.stream().collect(Collectors.groupingBy(StatisticsInfoHelper::getMergeEdgeKey));
        for(List<Edge> list : mergeEdgeListMap.values()) {
            Edge edge = list.get(0);
            MergeEdge mergeEdge = BeanUtil.copyProperties(edge, MergeEdge.class);
            mergeEdge.setStrength(list.stream().mapToDouble(Edge::getStrength).sum());
            mergeEdge.setCount((long) list.size());
            mergeEdgeList.add(mergeEdge);
        }
        return mergeEdgeList;
    }

    public static List<MergeEdge> convertToMergeEdgeList(List<Edge> edgeList, Map<String, MergeEdgeStatisticsInfo> mergeEdgeStatisticsInfoMap){
        List<MergeEdge> mergeEdgeList = new ArrayList<>();

        //统计强度次数
        Map<String, List<Edge>> mergeEdgeListMap = edgeList.stream().collect(Collectors.groupingBy(StatisticsInfoHelper::getMergeEdgeKey));
        for(List<Edge> list : mergeEdgeListMap.values()) {
            Edge edge = list.get(0);
            MergeEdge mergeEdge = BeanUtil.copyProperties(edge, MergeEdge.class);

            MergeEdgeStatisticsInfo mergeEdgeStatisticsInfo = mergeEdgeStatisticsInfoMap.get(StatisticsInfoHelper.getMergeEdgeKey(edge));
            mergeEdge.setStrength(mergeEdgeStatisticsInfo.getStrength());
            mergeEdge.setCount(mergeEdgeStatisticsInfo.getCount());
            mergeEdgeList.add(mergeEdge);
        }
        return mergeEdgeList;
    }

    public static Map<String, Vertex> convertVertexListToVertexMap(List<Vertex> vertexList) {
        Map<String, Vertex> vertexMap = vertexList.stream().collect(Collectors.toMap(Vertex::getId, a -> a, (k1, k2) -> k1));
        return vertexMap;
    }
    /**
     * 获取汇总正反边邻接表合并后的总表
     * @return 汇总后的总表
     */
    public static Map<Vertex, Set<MergeEdge>> getAdjacencyEdgeMapWithoutDirection(){
        Space space = SpaceContextHolder.get();
        /**
         * 将逆邻接矩阵合并至邻接矩阵
         */
        Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap = new HashMap<>();
        //初始化邻接矩阵
        for(Map.Entry<Vertex, Set<MergeEdge>> entry : space.getMergeEdgeAdjacencyTableMapByCard().entrySet()) {
            adjacencyEdgeMap.put(entry.getKey(), new HashSet<>(entry.getValue()));
        }
        //扩展逆向邻接表
        for (Map.Entry<Vertex, Set<MergeEdge>> entry : space.getReverseMergeEdgeAdjacencyTableMapByCard().entrySet()) {
            if (adjacencyEdgeMap.containsKey(entry.getKey())) {
                adjacencyEdgeMap.get(entry.getKey()).addAll(entry.getValue());
            }
            else {
                adjacencyEdgeMap.put(entry.getKey(), new HashSet<>(entry.getValue()));
            }
        }
        /**
         * 合并结束
         */
        return adjacencyEdgeMap;

    }



}
