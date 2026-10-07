package com.qianjiangsoft.graphcore.manager;

import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.param.FilterEdgeParam;
import com.qianjiangsoft.graphcore.param.FilterVertexParam;
import com.qianjiangsoft.graphcore.param.SortingParam;

import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * @author marshallwu
 * @date 2022/6/16 10:48
 */
public interface GraphAnalyseManager {

     /**
      * 对点进行排名
      * @param: vertexList
      * @param: vertexStatisticsInfoMap
      * @param: sortingParam
      * @param: edgeType
      * @return: java.util.List<com.qianjiangsoft.graphcore.model.Vertex>
      * @author: marshall
      * @date: 2022/6/16
     */
     List<Vertex> sortingVertexList(List<Vertex> vertexList, Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap,
                                    SortingParam sortingParam, EdgeType edgeType);

     /**
      * 对边进行排名
      * @param: mergeEdgeList
      * @param: sortingParam
      * @return: java.util.List<com.qianjiangsoft.graphcore.model.MergeEdge>
      * @author: marshall
      * @date: 2022/6/16
     */
     List<MergeEdge> sortingEdgeList(List<MergeEdge> mergeEdgeList, SortingParam sortingParam);

     /**
      * 对合并边过滤排名
      * @param edgeSet
      * @param filterEdgeParam
      * @param sortingParam
      * @return
      */

     List<MergeEdge> filterAndSortingMergeEdgeListByParam(Set<MergeEdge> edgeSet, FilterEdgeParam filterEdgeParam, SortingParam sortingParam);

     /**
      * 对原始边过滤排名
      * @param edgeSet
      * @param mergeEdgeStatisticsInfoMap
      * @param filterEdgeParam
      * @param sortingParam
      * @return
      */

     List<MergeEdge> filterAndSortingEdgeListByParam(Set<Edge> edgeSet, Map<String, MergeEdgeStatisticsInfo> mergeEdgeStatisticsInfoMap, FilterEdgeParam filterEdgeParam, SortingParam sortingParam);

     List<Vertex> filterVertexListByParam(List<Vertex> vertexList, FilterVertexParam filterVertexParam);
}
