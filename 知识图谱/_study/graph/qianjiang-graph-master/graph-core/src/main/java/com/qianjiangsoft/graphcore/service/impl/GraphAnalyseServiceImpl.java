package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollectionUtil;
import com.qianjiangsoft.graphcore.constants.ConditionType;
import com.qianjiangsoft.graphcore.constants.ExtendDirection;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.param.PointCount;
import com.qianjiangsoft.graphcore.helper.ModelHelper;
import com.qianjiangsoft.graphcore.manager.GraphAnalyseManager;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.param.*;
import com.qianjiangsoft.graphcore.service.GraphAnalyseService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.*;
import java.util.stream.Collectors;

import static com.qianjiangsoft.graphcore.helper.GraphAnalyseHelper.checkCondition;

@Service
@Slf4j
public class GraphAnalyseServiceImpl implements GraphAnalyseService {

    @Autowired
    GraphAnalyseManager graphAnalyseManager;
    private static String splitChart =":";

    @Override
    public List<Edge> findSNAVertexByActivity() {
        return null;
    }

    @Override
    public List<Edge> findSNAVertexByCentrality() {
        return null;
    }

    @Override
    public List<Edge> findSNAVertexByImportance() {
        return null;
    }

    @Override
    public List<Edge> findStrongestCommunity() {
        return null;
    }

    @Override
    public List<MergeEdge> searchSubgraph(SearchSubgraphParam searchSubgraphParam) {

        //判断入参是否符合条件
        if (searchSubgraphParam.getStartPointList().size() == 0 || searchSubgraphParam.getMaxStep() == 0) {
            return null;
        }

//        Space space = SpaceContextHolder.getThreadLocal();

        //获得正向和反向邻接表的组合
        Map<Vertex, Set<MergeEdge>> adjacencyTableMap = ModelHelper.getAdjacencyEdgeMapWithoutDirection();

        List<MergeEdge> mergeEdgeList = new ArrayList<>();

        Deque<MergeEdge> tmpList = new LinkedList<>();
        HashSet<MergeEdge> resList = new HashSet<>();

        List<SearchSubgraphEdgeParam> paramList = searchSubgraphParam.getEdgeParamList();


        Set<String> userDefineVertex = new HashSet<>(); //参数中用户选择的实体列表
        Set<String> participateVertex =new HashSet<>();  //实际参与运算的实体列表

        searchSubgraphParam.getStartPointList().forEach(x -> userDefineVertex.add(x.getId()));//获取用户参数节点列表

        Set<MergeEdge> visitedEdge = new HashSet<>();   //已访问过的边
        Set<MergeEdge> firstLayResultEdgeList = new HashSet<>();  //当前层级符合条件的边列表
        Map<MergeEdge, String> PointToEdge = new HashMap<>();   //每条线段来源于哪个点
        //针对每个实体进行深度遍历，以最大努力找到连通图
        for (String startPoint : userDefineVertex) {
//            List<MergeEdge> firstLayEdgeListForPoint = new ArrayList<>();
//            String startPointOfres=startPoint;
            Set<MergeEdge> nextEdgeSet = adjacencyTableMap.get(SpaceContextHolder.get().getVertexMap().get(startPoint));
            if (nextEdgeSet == null) {
                continue;
            }
            //如果没有边类型约束则所有边加入迭代
            if (CollectionUtil.isEmpty(paramList)) {
                firstLayResultEdgeList.addAll(nextEdgeSet);
                participateVertex.add(startPoint);
                for (MergeEdge list : nextEdgeSet) {
                    PointToEdge.put(list, startPoint);  //每条边从哪个点来的
                }
            } else {
                //查找邻接表里有没有符合条件的边
                Set<MergeEdge> rsOfCurVertex=findEdgeWithCondition(nextEdgeSet, searchSubgraphParam);
                //如果当前节点没有符合条件的边，则找对端节点是否有对应的边（匹配用户选择的是嫌疑人）
                if(rsOfCurVertex==null||rsOfCurVertex.isEmpty())
                {
                    for(MergeEdge nextEdge:nextEdgeSet){
                        Vertex oppositeVertex= nextEdge.getDestination().getId().equals(startPoint)? nextEdge.getSource():nextEdge.getDestination();
                        Set<MergeEdge> oppositeVertexNextEdgeSet = adjacencyTableMap.get(oppositeVertex);
                        Set<MergeEdge> mateEdgeSetForOpposite=findEdgeWithCondition(oppositeVertexNextEdgeSet, searchSubgraphParam);
                        if(mateEdgeSetForOpposite!=null&& !mateEdgeSetForOpposite.isEmpty())
                        {
//                            startPointOfres=oppositeVertex.getId();
                            firstLayResultEdgeList.addAll(mateEdgeSetForOpposite);
                            participateVertex.add(oppositeVertex.getId());
                            resList.add(nextEdge);
                            for (MergeEdge list : mateEdgeSetForOpposite) {
                                PointToEdge.put(list, oppositeVertex.getId());  //每条边从哪个点来的
                            }
                        }
                    }

                }else
                {
                    firstLayResultEdgeList.addAll(rsOfCurVertex);
                    participateVertex.add(startPoint);
                    for (MergeEdge list : rsOfCurVertex) {
                        PointToEdge.put(list, startPoint);  //每条边从哪个点来的
                    }
                }

            }
//            for (MergeEdge list : firstLayResultEdgeList) {
//                PointToEdge.put(list, startPointOfres);  //每条边从哪个点来的
//            }
//            firstLayEdgeList.addAll(firstLayEdgeListForPoint);
        }

        Set<MergeEdge> deleteLayEdgeList = new HashSet<>();
        //针对第一层的所有边进行最大、最小排序
//        Set<MergeEdge> firstLayResultEdgeList = firstLayEdgeList;
//        Set<MergeEdge> firstLayResultEdgeList = caculateSortEdge(firstLayEdgeList,searchSubgraphParam,
//        deleteLayEdgeList);
        Map<MergeEdge, Set<MergeEdge>> roadMap = new HashMap<>();
        for (MergeEdge edge : firstLayResultEdgeList) {
            visitedEdge.add(edge);
            Set<MergeEdge> road = new HashSet<MergeEdge>();
            road.add(edge);
            roadMap.put(edge, road);
        }
        Set<MergeEdge> ignoreEdgeSet = new HashSet<>();
        Set<String> starVertex = new HashSet<>();

        subgraphBacktrack(1, participateVertex, visitedEdge, ignoreEdgeSet, starVertex, PointToEdge,
                searchSubgraphParam, resList, firstLayResultEdgeList, roadMap, adjacencyTableMap);

        //如果点只关联1个边且不是用户选择的点，则去除该边
        HashMap<Vertex, PointCount> vertexEdgeCount= new HashMap<>();
        for (MergeEdge edge : resList) {
            Vertex startPoint = edge.getSource();
            Vertex endPoint = edge.getDestination();
            countVetexEdgeCount(vertexEdgeCount, startPoint, edge);
            countVetexEdgeCount(vertexEdgeCount, endPoint, edge);
        }
        for (Vertex v : vertexEdgeCount.keySet()) {
            PointCount pc = vertexEdgeCount.get(v);
            if (pc.getCount() == 1 && !userDefineVertex.contains(v.getId())) {
                resList.removeAll(pc.getEdgeList());
                List<MergeEdge> list = new ArrayList<>(pc.getEdgeList());
                pc.setCount(pc.getCount() - 1);
                deleteVertex(vertexEdgeCount, list.get(0), userDefineVertex, pc.getVertex(), resList);
            }
        }
        //加上被扩展节点

        mergeEdgeList = new ArrayList<>(new LinkedList<>(resList));
        log.info("Connected Graph return egdes:"+mergeEdgeList.size());
        return mergeEdgeList;
    }

    private void deleteVertex(HashMap<Vertex, PointCount> vertexEdgeCounte, MergeEdge deleteEdge,
                              Set<String> userDefineVertex, Vertex aloneVertex, HashSet<MergeEdge> resList) {
        PointCount pc_new = vertexEdgeCounte.get(deleteEdge.getSource().getId().equals(aloneVertex.getId()) ?
                deleteEdge.getDestination() : deleteEdge.getSource());
        pc_new.setCount(pc_new.getCount() - 1);
        if (pc_new.getCount() == 1 && !userDefineVertex.contains(pc_new.getVertex())) {
            List<MergeEdge> edgeList = new ArrayList<>(pc_new.getEdgeList());
            MergeEdge edge = edgeList.get(0);
            resList.removeAll(pc_new.getEdgeList());
            pc_new.setCount(pc_new.getCount() - 1);
            deleteVertex(vertexEdgeCounte, edge, userDefineVertex, pc_new.getVertex(), resList);
        }
    }

    /**
     * 计算每个点关联边的数量
     *
     * @param vertexEdgeCounte
     * @param vertex
     * @param edge
     */
    private void countVetexEdgeCount(HashMap<Vertex, PointCount> vertexEdgeCounte, Vertex vertex, MergeEdge edge) {
        if (vertexEdgeCounte.get(vertex) == null) {
            PointCount vertexCount = new PointCount();
            vertexCount.setVertex(vertex);
            vertexCount.getEdgeList().add(edge);
            vertexCount.setCount(vertexCount.getCount() + 1);
            vertexEdgeCounte.put(vertex, vertexCount);
        } else {
            PointCount vertexCount = vertexEdgeCounte.get(vertex);
            vertexCount.getEdgeList().add(edge);
            vertexCount.setCount(vertexCount.getCount() + 1);
        }
    }


    /**
     * 查找当前节点符合类型、强度、次数的边（除最大、最小以外）
     *
     * @param nextEdge            邻接表搜出的边的集合
     * @param searchSubgraphParam 过滤参数
     * @return
     */
    private Set<MergeEdge> findEdgeWithCondition(Set<MergeEdge> nextEdge,
                                                  SearchSubgraphParam searchSubgraphParam) {

        List<SearchSubgraphEdgeParam> paramList = searchSubgraphParam.getEdgeParamList();
        Set<MergeEdge> currentLayEdgeList = new HashSet<>();
        List<MergeEdge> allNextEdgeList = new ArrayList<>(nextEdge);
        if (paramList.get(0) == null) {
            return new HashSet<>(nextEdge);
        }
        for (MergeEdge e : allNextEdgeList) {
            boolean contType = false;  //边是否符合用户指定的类型条件
            boolean rightEdge = true;
            for (SearchSubgraphEdgeParam param : paramList) {
                EdgeType edgeType = param.getEdgeType();
                Condition strengthCondition = param.getStrengthCondition();  //强度参数
                Condition countCondition = param.getCountCondition();        //次数参数
                if (e.getType().equals(edgeType)) {
                    contType = true;
                    if (strengthCondition.getConditionType() != null) {
                        switch (strengthCondition.getConditionType()) {
                            case ConditionType
                                    .GT:
                                if (e.getStrength() <= Double.parseDouble(strengthCondition.getValue())) {
                                    rightEdge = false;
                                }
                                break;
                            case ConditionType.LT:
                                if (e.getStrength() >= Double.parseDouble(strengthCondition.getValue())) {
                                    rightEdge = false;
                                }
                                break;
                            case ConditionType.EQ:
                                if (e.getStrength() != Double.parseDouble(strengthCondition.getValue())) {
                                    rightEdge = false;
                                }
                                break;
                        }
                    }
                    if (countCondition.getConditionType() != null) {
                        switch (countCondition.getConditionType()) {
                            case ConditionType
                                    .GT:
                                if (e.getCount() <= Double.parseDouble(countCondition.getValue())) {
                                    rightEdge = false;
                                }
                                break;
                            case ConditionType.LT:
                                if (e.getCount() >= Double.parseDouble(countCondition.getValue())) {
                                    rightEdge = false;
                                }
                                break;
                            case ConditionType.EQ:
                                if (e.getCount() != Double.parseDouble(countCondition.getValue())) {
                                    rightEdge = false;
                                }
                                break;
                        }
                    }
                }
            }
            if (contType && rightEdge) {
                currentLayEdgeList.add(e);
            }
        }
        return currentLayEdgeList;
    }

    /**
     * 针对当前层级的所有边做最大最小过滤
     *
     * @param currentLayEdgeList
     * @param searchSubgraphParam
     * @return 经过强度、次数排序后的边
     */
    private Set<MergeEdge> caculateSortEdge(Set<MergeEdge> currentLayEdgeList,
                                            SearchSubgraphParam searchSubgraphParam, Set<MergeEdge> deleteLayEdgeList) {
        List<MergeEdge> currentLayResultEdgeList = new LinkedList<>(); //针对本层所有链接做最大最小判断
        List<SearchSubgraphEdgeParam> paramList = searchSubgraphParam.getEdgeParamList();
//        Boolean isNeedCaculate = true;
        //判断是否需要过滤
        if (paramList.get(0) == null) {
            return currentLayEdgeList;

        } else {
            for (SearchSubgraphEdgeParam param : paramList) {
                if (param.getSorting() == null) {
                    continue;
                } else {
                    Condition strengthCondition = param.getStrengthCondition();  //强度参数
                    Condition countCondition = param.getCountCondition();        //次数参数
                    SortingParam sorting = param.getSorting();
                    Integer limit = sorting.getLimit();

                    boolean isNeedDelete = true;
                    List<MergeEdge> edgeList = new LinkedList<>();
                    List<MergeEdge> removeEdgeList = new LinkedList<>();
                    for (MergeEdge edge : currentLayEdgeList) {
                        if (edge.getType().equals(param.getEdgeType())) {
                            edgeList.add(edge);
                        } else {
                            removeEdgeList.add(edge);
                        }
                    }
                    currentLayEdgeList.removeAll(removeEdgeList);
                    //如果长度小于limit则不需要delete
                    if (edgeList.size() < limit) {
                        isNeedDelete = false;
                    }
                    //对过滤出的第N层边做强度、次数的最大最小最排序
                    if (strengthCondition.getConditionType() != null) {
                        edgeList.sort(Comparator.comparing(MergeEdge::getStrength));
                        switch (strengthCondition.getConditionType()) {
                            case ConditionType.MAX:
                                if (edgeList.size() > limit) {
                                    edgeList = edgeList.subList(0, limit);
                                }
                                break;
                            case ConditionType.MIN:
                                Collections.reverse(edgeList);
                                if (edgeList.size() > limit) {
                                    edgeList = edgeList.subList(0, limit);
                                }
                                break;
                        }
                    }
                    if (countCondition.getConditionType() != null) {
                        edgeList.sort(Comparator.comparing(MergeEdge::getCount));
                        switch (countCondition.getConditionType()) {
                            case ConditionType.MAX:
                                if (edgeList.size() > limit) {
                                    edgeList = edgeList.subList(0, limit);
                                }
                                break;
                            case ConditionType.MIN:
                                Collections.reverse(edgeList);
                                if (edgeList.size() > limit) {
                                    edgeList = edgeList.subList(0, limit);
                                }
                                break;
                        }
                    }
                    if (isNeedDelete) {
                        deleteLayEdgeList.addAll(edgeList.subList(limit, edgeList.size()));
                    }
                    currentLayResultEdgeList.addAll(edgeList);
                }
            }
            //排序计算后剩下的边，没有被设置排序的类型的边
            if (currentLayEdgeList.size() > 0) {
                currentLayResultEdgeList.addAll(currentLayEdgeList);
            }
        }

        return new HashSet<MergeEdge>(currentLayResultEdgeList);
    }


    /**
     * 迭代查找符合条件的连通子图，判断边是否关联到用户指定的节点
     *
     * @param currentDeep         当前深度
     * @param userDefineVertex    用户指定的点
     * @param visitedEdge         已访问过的边
     * @param ignoreEdgeSet       需要忽略的线段
     * @param starVertex          明星点，通过他肯定可以接入内联路径
     * @param PointToEdge         边是从哪个点扩出来的
     * @param searchSubgraphParam 用户指定的参数
     * @param resList             结果集
     * @param curLayerList        当前层的所有边
     * @param roadMap             边所属的路径
     * @param adjacencyTableMap   邻接表
     */
    private void subgraphBacktrack(int currentDeep, Set<String> userDefineVertex, Set<MergeEdge> visitedEdge,
                                   Set<MergeEdge> ignoreEdgeSet, Set<String> starVertex,
                                   Map<MergeEdge, String> PointToEdge, SearchSubgraphParam searchSubgraphParam
            , HashSet<MergeEdge> resList, Set<MergeEdge> curLayerList, Map<MergeEdge, Set<MergeEdge>> roadMap,
                                   Map<Vertex, Set<MergeEdge>> adjacencyTableMap) {


        List<SearchSubgraphEdgeParam> paramList = searchSubgraphParam.getEdgeParamList();
        Set<MergeEdge> nextLayEdgeList = new HashSet<>();
        if (currentDeep > searchSubgraphParam.getMaxStep()) {
            return;
        }

        //针对本层所有边进行遍历
        for (MergeEdge startEdge : curLayerList) {
//            log.info("start the edge:"+startEdge.getSource().getName()+"  to "+startEdge.getDestination().getName());
            /**
             * 确定从哪个端点开始扩边
             */
            String destPoint;
            String sPoint = PointToEdge.get(startEdge);
            Set<MergeEdge> nextLayEdgeListForCurPoint = new HashSet<>();
            if (ignoreEdgeSet.contains(startEdge)) {
                continue;
            }

            //线段两端都是用户选择的点则直接加入
            if (userDefineVertex.contains(startEdge.getDestination().getId()) && userDefineVertex.contains(startEdge.getSource().getId())) {
                resList.add(startEdge);
                starVertex.add(startEdge.getDestination().getId());
                starVertex.add(startEdge.getSource().getId());
                continue;
            } else if (userDefineVertex.contains(startEdge.getDestination().getId()) || userDefineVertex.contains(startEdge.getSource().getId())) {
                if (roadMap.get(startEdge).size() > 1) {//如果有一端在用户定义的点上，且路径长度大于1，则将路径加入
                    if (checkRoad(roadMap, startEdge, new ArrayList<MergeEdge>(roadMap.get(startEdge)),
                            userDefineVertex, starVertex, resList)) {
                        if (userDefineVertex.contains(startEdge.getDestination())) {
                            starVertex.add(startEdge.getSource().getId());
                        } else {
                            starVertex.add(startEdge.getDestination().getId());
                        }
                        continue;
                    }
                }
            }

            if (sPoint.equals(startEdge.getSource().getId())) {//这里是手工建的点所以不能直接用equal
                destPoint = startEdge.getDestination().getId();
            } else {
                destPoint = startEdge.getSource().getId();
            }

            //扩展到起始点了
            if (userDefineVertex.contains(destPoint)) {
                continue;
            }
            Set<MergeEdge> nextEdge = adjacencyTableMap.get(SpaceContextHolder.get().getVertexMap().get(destPoint));
            if (nextEdge == null) {  //这个应该不可能，考虑去掉
                continue;
            }

            //如果对边类型没有要求则判断是否以访问过，未访问则直接加入
            if (paramList.get(0) == null) { 
                for (MergeEdge edge : nextEdge) {
                    nextLayEdgeListForCurPoint.add(edge);
                }
            } else {
                nextLayEdgeListForCurPoint.addAll(findEdgeWithCondition(nextEdge, searchSubgraphParam));
            }
            if (nextLayEdgeListForCurPoint.size() == 0) {
                continue;
            }
            if (nextLayEdgeListForCurPoint.size() == 1) {
                List<MergeEdge> oneEdge = new ArrayList<>(nextLayEdgeListForCurPoint);
//                roadMap.remove(oneEdge.get(0));
//                visitedEdge.remove(oneEdge.get(0));因为保留了已访问状态，所以下次再遍历到这条边时会直接调用roadmap，但这时roadmap已经没有他的数据了
//                resList.remove(oneEdge.get(0));
                ignoreEdgeSet.add(oneEdge.get(0));
                continue;
            }
            /**
             *  如果destPoint是已经有符合路径的点，那么连通该点的路径都应该是内联路径
             */
            if (starVertex.contains(destPoint)) {
                for (MergeEdge edge : nextLayEdgeListForCurPoint) {
                    Set<MergeEdge> edgeList = roadMap.get(startEdge);
                    HashSet<MergeEdge> s = new HashSet<>(edgeList);
                    s.add(edge);
                    resList.addAll(s);
                    roadMap.put(edge, s);
                    PointToEdge.put(edge, destPoint);
                }
            } else {  //如果没有经过明星点，则对路径做判断
                //去除从起始点发出的两步回路
                if (nextLayEdgeListForCurPoint.size() == 2) {
                    boolean isLoop = true;
                    for (MergeEdge edge : nextLayEdgeListForCurPoint) {
                        if (!userDefineVertex.contains(edge.getDestination().getId()) && !userDefineVertex.contains(edge.getSource().getId())) {
                            isLoop = false;
                        }
                    }
                    if (isLoop)
                        continue;
                }
                /**
                 * 针对已访问的edge进行点集合并，判断是否有内联路径
                 */

                for (MergeEdge edge : nextLayEdgeListForCurPoint) {//路径撞上了
                    if (edge.equals(startEdge)) {
                        continue;
                    }
                    if (visitedEdge.contains(edge)) {//出现重复边
                        List<MergeEdge> allRoad = new ArrayList<>();
                        Set<MergeEdge> curRoad = roadMap.get(startEdge);
                        Set<MergeEdge> counterRoad = roadMap.get(edge);//找出路径
                        if (counterRoad != null) {
                            allRoad.addAll(counterRoad);
                            allRoad.addAll(curRoad);
                            checkRoad(roadMap, edge, allRoad, userDefineVertex, starVertex, resList);
                            PointToEdge.put(edge, destPoint);
                        }
//                        continue;
                    } else { //没有出现重复边
                        if (!ignoreEdgeSet.contains(edge)) {
                            visitedEdge.add(edge);
                            PointToEdge.put(edge, destPoint);
                            Set<MergeEdge> fatherRoad = roadMap.get(startEdge);  //获取上一条边的全部路径
                            Set<MergeEdge> childRoad2 = new HashSet<>(fatherRoad);
                            childRoad2.add(edge);
                            roadMap.put(edge, childRoad2);
                        }
                    }
                }
                nextLayEdgeListForCurPoint.remove(startEdge);//对端所有链接中删除本轮迭代的边
            }

            nextLayEdgeList.addAll(nextLayEdgeListForCurPoint);
        }
        Set<MergeEdge> deleteLayEdgeList = new HashSet<>();
        //针对第一层的所有边进行最大、最小排序
        Set<MergeEdge> CurrentLayResultEdgeList = nextLayEdgeList;
//        Set<MergeEdge> CurrentLayResultEdgeList = caculateSortEdge(nextLayEdgeList,searchSubgraphParam,
//        deleteLayEdgeList);
//
//        for(MergeEdge edge:deleteLayEdgeList){
//            roadMap.remove(edge);
//            visitedEdge.remove(edge);
//        }
        //广度遍历
        currentDeep++;
        subgraphBacktrack(currentDeep, userDefineVertex, visitedEdge, ignoreEdgeSet, starVertex, PointToEdge,
                searchSubgraphParam, resList, CurrentLayResultEdgeList, roadMap, adjacencyTableMap);
    }


    /**
     * 碰到已访问边后进行路径碰撞判断
     *
     * @param roadMap
     * @param edge
     * @param allRoad
     * @param userDefineVertex
     * @param starVertex
     * @param resList
     */
    private boolean checkRoad(Map<MergeEdge, Set<MergeEdge>> roadMap, MergeEdge edge, List<MergeEdge> allRoad,
                              Set<String> userDefineVertex, Set<String> starVertex, Set<MergeEdge> resList) {
        boolean result = false;
        Set<Vertex> countVertex = new HashSet<>();
        for (MergeEdge r : allRoad) {//另一种方法，计算路径融合后点集
            if (userDefineVertex.contains(r.getSource().getId())) {
                countVertex.add(r.getSource());
            }
            if (userDefineVertex.contains(r.getDestination().getId())) {
                countVertex.add(r.getDestination());
            }
        }
        if (countVertex.size() > 1) {
            resList.addAll(allRoad);
            starVertex.add(edge.getDestination().getId());
            starVertex.add(edge.getSource().getId());
            result = true;
        }
//        else{
        roadMap.put(edge, new HashSet<>(allRoad));
//        HashSet<MergeEdge> s = new HashSet<>(allRoad);
//        roadMap.put(edge, new HashSet<>(s));

//        }
        return result;
    }


    @Override
    public List<MergeEdge> extendGraphDataV2(ExtendGraphDataParam extendGraphDataParam) {

        Space space = SpaceContextHolder.get();
        List<MergeEdge> mergeEdgeList = new ArrayList<>();
        bfs(extendGraphDataParam.getStartPoint(), 0, new HashSet<>(), extendGraphDataParam, space, mergeEdgeList);
//       return mergeEdgeList;
        //从结果数据中删除步数小于最小步数的边
        if (extendGraphDataParam.getMinStep() == 1) {
            return mergeEdgeList;
        } else {
            return extendFilter(mergeEdgeList, space, extendGraphDataParam);
        }
    }

    /**
     * 基于扩展的结果，再进行一次深度遍历，并删掉路径小于最小步数的路径
     *
     * @param list                 扩展的计算结果
     * @param space
     * @param extendGraphDataParam 用户的参数
     * @return
     */
    private List<MergeEdge> extendFilter(List<MergeEdge> list, Space space, ExtendGraphDataParam extendGraphDataParam) {
        Deque<MergeEdge> tmpList = new LinkedList<>();
        Set<MergeEdge> visitedEdge = new HashSet<>();
        int minStep = extendGraphDataParam.getMinStep();
        ExtendDirection direction = extendGraphDataParam.getDirection();

        List<List<MergeEdge>> resultEdgeList = new ArrayList<>();
        Vertex startPoint = extendGraphDataParam.getStartPoint();
        List<MergeEdge> mergeEdgeList = new ArrayList<>();
        Set<MergeEdge> nextEdgeList = new HashSet<>();
        int maxStep = extendGraphDataParam.getMaxStep();
//        log.info("the listsize is:"+list.size());
        //基于结果集做邻接表
//        Map<Vertex, Set<MergeEdge>> mergeEdgeAdjacencyTableMap = list.stream().collect(
//                Collectors.groupingBy(MergeEdge::getSource, Collectors.toSet()));
//        Map<Vertex, Set<MergeEdge>> reverseMergeEdgeAdjacencyTableMap = list.stream().collect(
//                Collectors.groupingBy(MergeEdge::getDestination, Collectors.toSet()));
        Map<Vertex, Set<MergeEdge>> mergeEdgeAdjacencyTableMap = new HashMap<>();
        Map<Vertex, Set<MergeEdge>> reverseMergeEdgeAdjacencyTableMap = new HashMap<>();
        for (MergeEdge l : list) {
            if (mergeEdgeAdjacencyTableMap.containsKey(l.getSource())) {
                mergeEdgeAdjacencyTableMap.get(l.getSource()).add(l);
            } else {
                HashSet<MergeEdge> newSet = new HashSet<MergeEdge>();
                newSet.add(l);
                mergeEdgeAdjacencyTableMap.put(l.getSource(), newSet);
            }
            if (reverseMergeEdgeAdjacencyTableMap.containsKey(l.getDestination())) {
                reverseMergeEdgeAdjacencyTableMap.get(l.getDestination()).add(l);
            } else {
                HashSet<MergeEdge> newSet = new HashSet<MergeEdge>();
                newSet.add(l);
                reverseMergeEdgeAdjacencyTableMap.put(l.getDestination(), newSet);
            }

        }

        log.info(" the mergeEdgeAdjacencyTableMap size is" + mergeEdgeAdjacencyTableMap.size());

        log.info(" the reverseMergeEdgeAdjacencyTableMap size is" + reverseMergeEdgeAdjacencyTableMap.size());
        switch (extendGraphDataParam.getDirection()) {
            case out:
                break;
            case in:
                mergeEdgeAdjacencyTableMap = reverseMergeEdgeAdjacencyTableMap;
                break;
            case both:
                for (Vertex key : reverseMergeEdgeAdjacencyTableMap.keySet()) {
                    if (mergeEdgeAdjacencyTableMap.containsKey(key)) {
                        mergeEdgeAdjacencyTableMap.get(key).addAll(reverseMergeEdgeAdjacencyTableMap.get(key));
                    } else {
                        mergeEdgeAdjacencyTableMap.put(key, reverseMergeEdgeAdjacencyTableMap.get(key));
                    }
                }
        }

        //查找点的边
        nextEdgeList = mergeEdgeAdjacencyTableMap.get(startPoint);
        if (nextEdgeList != null) {
            for (MergeEdge nextEdge : nextEdgeList) {
                if (!visitedEdge.contains(nextEdge)) {
                    tmpList.offerLast(nextEdge);
                    visitedEdge.add(nextEdge);
                    filterPath(maxStep, minStep, startPoint, startPoint, nextEdge, tmpList, resultEdgeList,
                            mergeEdgeAdjacencyTableMap,
                            visitedEdge, direction);
                    visitedEdge.remove((nextEdge));
                    tmpList.pollLast();
                }
            }
        }

        for (List<MergeEdge> l : resultEdgeList) {
            if (l.size() >= minStep) {
                mergeEdgeList.addAll(l);
            }
        }
        //将路径数组，变为边的数组
        HashSet<MergeEdge> edges = new HashSet<>(mergeEdgeList);
        List<MergeEdge> mergeEdgeList1 = new LinkedList<>(edges);
        return mergeEdgeList1;
//        return resultEdgeList;
    }

    /**
     * 深度遍历查找路径
     *
     * @param maxStep          最大步数
     * @param minStep          最小步数
     * @param startPoint       起点
     * @param literatePoint    上一步迭代的边
     * @param literateEdge     从上一步的点literatePoint找出的一系列边中的一条边
     * @param tmpList          临时路径
     * @param edgeList         结果边集
     * @param adjacencyEdgeMap 已经组装好的邻接举证
     * @param visitedEdge      已访问过的边
     * @param direction        扩展时选择的方向
     */
    private void filterPath(int maxStep, int minStep, Vertex startPoint, Vertex literatePoint, MergeEdge literateEdge
            , Deque<MergeEdge> tmpList,
                            List<List<MergeEdge>> edgeList, Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap,
                            Set<MergeEdge> visitedEdge, ExtendDirection direction) {
        Vertex destNode = null;
        //根据方向取出下个节点
        switch (direction) {
            case out:
                destNode = literateEdge.getDestination();
                break;

            case in:
                destNode = literateEdge.getSource();
                break;

            case both:
                destNode = literateEdge.getSource().equals(literatePoint) ? literateEdge.getDestination() :
                        literateEdge.getSource();
                break;
        }
        //判断destNode就是startPoint的时候迭代应该终止,即这条边是指向起点的
        if (destNode.equals(startPoint)) {
            //判断当前路径是否满足要求
            if (tmpList.size() >= minStep) {
                edgeList.add(new LinkedList<>(tmpList));
            }
            return;
        }
        //新的对端所拥有的所有边
        Set<MergeEdge> nextEdgeList = adjacencyEdgeMap.get(destNode);
//        log.info("the vertex :"+destNode.getName()+"'s edge size is"+(nextEdgeList==null?"null":nextEdgeList.size()));
        /**
         * 如果没有下一个点，且步数达到最大值则将该路径放入结果集
         * 两个条件，1：路径超过最大步数；2：路径没有下一个节点，且步数大于等于最小步数
         */
        if (tmpList.size() >= maxStep) {
            edgeList.add(new LinkedList<>(tmpList));
            return;
        } else if ((nextEdgeList == null || nextEdgeList.size() == 1) && tmpList.size() >= minStep)  //nextEdgeList
        // 至少是1，因为这个点是从父节点遍历过来的
        {
            edgeList.add(new LinkedList<>(tmpList));
            return;
        }

        if (nextEdgeList == null) {
            return;
        }

        for (MergeEdge nextEdge : nextEdgeList) {
            if (!visitedEdge.contains(nextEdge)) {
                tmpList.offerLast(nextEdge);
                visitedEdge.add(nextEdge);
                filterPath(maxStep, minStep, startPoint, destNode, nextEdge, tmpList, edgeList, adjacencyEdgeMap,
                        visitedEdge,
                        direction);
                visitedEdge.remove((nextEdge));
                tmpList.pollLast();
            }
        }
    }


    private void bfs(Vertex startPoint, int depth, Set<MergeEdge> visitedEdgeSet,
                     ExtendGraphDataParam extendGraphDataParam, Space space, List<MergeEdge> result) {

        //深度累加
        depth = depth + 1;

        //判断深度
        if (depth > extendGraphDataParam.getMaxStep()) {
            return;
        }

        //查询所有点相连的边
        List<MergeEdge> edgeList = new ArrayList<>();

        switch (extendGraphDataParam.getDirection()) {
            case out:
                CollectionUtil.addAll(edgeList, space.getMergeEdgeAdjacencyTableMapByCard().get(startPoint));
                break;
            case in:
                CollectionUtil.addAll(edgeList, space.getReverseMergeEdgeAdjacencyTableMapByCard().get(startPoint));
                break;
            case both:
                CollectionUtil.addAll(edgeList, space.getMergeEdgeAdjacencyTableMapByCard().get(startPoint));
                CollectionUtil.addAll(edgeList, space.getReverseMergeEdgeAdjacencyTableMapByCard().get(startPoint));
                break;
            default:

        }

        //按类型分组
        Map<EdgeType, List<MergeEdge>> edgeListGroupByType =
                edgeList.stream().collect(Collectors.groupingBy(MergeEdge::getType));

        Set<Vertex> childVertexList = new HashSet<>();

        //过滤排序
        for (EdgeType edgeType : edgeListGroupByType.keySet()) {

            //判断类型是否满足
            if (extendGraphDataParam.getEdgeTypeList().contains(edgeType)) {

                //过滤重复访问
                List<MergeEdge> oneTypeEdgeList =
                        edgeListGroupByType.get(edgeType).stream().filter(edge ->
                                !visitedEdgeSet.contains(BeanUtil.copyProperties(edge, MergeEdge.class)))
                                .collect(Collectors.toList());

                //对一个类型边过滤排序
                FilterEdgeParam filterEdgeParam = BeanUtil.copyProperties(extendGraphDataParam, FilterEdgeParam.class);
                List<MergeEdge> oneTypeMergeEdgeList =
                        graphAnalyseManager.filterAndSortingMergeEdgeListByParam(new HashSet<>(oneTypeEdgeList),
                                filterEdgeParam, extendGraphDataParam.getSorting());

                //加入结果集
                result.addAll(oneTypeMergeEdgeList);

                //加入已访问边集合
                visitedEdgeSet.addAll(oneTypeMergeEdgeList);

                //加入下一层遍历,排除自己
                childVertexList.addAll(oneTypeMergeEdgeList.stream().map(MergeEdge::getDestination).collect(Collectors.toSet()));
                childVertexList.addAll(oneTypeMergeEdgeList.stream().map(MergeEdge::getSource).collect(Collectors.toSet()));
                childVertexList =
                        childVertexList.stream().filter(vertex -> !vertex.equals(startPoint)).collect(Collectors.toSet());
            }
        }

        //下一层遍历
        for (Vertex vertex : childVertexList) {
            bfs(vertex, depth, visitedEdgeSet, extendGraphDataParam, space, result);
        }
    }


    @Override
    public List<MergeEdge> analyseFlowDirection(AnalyseFlowDirectionParam param) {
        Space space = SpaceContextHolder.get();
        EdgeType type = param.getEdgeType();
        List<MergeEdge> mergeEdgeList1 = space.getMergeEdgeList();
        List<MergeEdge> mergeEdgeList2 = new ArrayList<>();
        List<MergeEdge> mergeEdgeList = mergeEdgeList1.stream().filter(s -> s.getType().equals(type)).collect(Collectors.toList());
        List<MergeEdge> reEdgeList = new ArrayList<>();
        HashMap<String,MergeEdge> resultEdgeMap =new HashMap<>(); //存储边
        for (int i = 0; i < mergeEdgeList.size(); i++) {

            MergeEdge literateEdge = mergeEdgeList.get(i);
            MergeEdge currentEdge=BeanUtil.copyProperties(literateEdge,MergeEdge.class);
            //计算强度
            caculateStrenth(reEdgeList, currentEdge,resultEdgeMap);
        }

        //对合并以后的边进行筛选
        if(BeanUtil.isNotEmpty(param.getStrengthCondition())) {
            for (int i = 0; i < reEdgeList.size(); i++) {
                //强转double型Strength为string类
                Double v1 = reEdgeList.get(i).getStrength();
                String v2 = param.getStrengthCondition().getConditionType();
                String v3 = param.getStrengthCondition().getValue();


                if (checkCondition(v1, v2, v3, null) == true) {
                    //将符合强度条件的边放入mergeEdgeList2中
                    mergeEdgeList2.add(reEdgeList.get(i));
                }
            }
        }else{
            mergeEdgeList2.addAll(reEdgeList);
        }
        return mergeEdgeList2;
    }

    /**
     * 比较两条边的起点和终点是否相同或相反或不同
     *
     * @param resEdge      结果集中的边
     * @param literateEdge 迭代出的边
     * @return 0：同向；1：反向；2：无关
     */
    private static int compareEdge(MergeEdge resEdge, MergeEdge literateEdge) {
        //同向，返回0
        if (resEdge.getSource().equals(literateEdge.getSource()) && resEdge.getDestination().equals(literateEdge.getDestination())) {
            return 0;
        }
        //反向，返回1
        else if (resEdge.getSource().equals(literateEdge.getDestination()) && resEdge.getDestination().equals(literateEdge.getSource()))
            return 1;
        else
            //无关，返回2
            return 2;

    }

    /**
     * 计算强度以及方向修正
     *
     * @param reedgeList   结果边的集合
     * @param literateEdge 迭代出来的边//合并边的集合
     */
    private static void caculateStrenth(List<MergeEdge> reedgeList, MergeEdge literateEdge,HashMap<String,MergeEdge> resultEdgeMap ) {
        int compairResult = 2;
        if (reedgeList.size() == 0) {
            reedgeList.add(literateEdge);
            resultEdgeMap.put(literateEdge.getSource().getId()+splitChart+literateEdge.getDestination().getId(),literateEdge);
            return;
        }
        //如果结果列表不为空
        String litKey= literateEdge.getSource().getId()+splitChart+literateEdge.getDestination().getId();
        MergeEdge aimEdge=resultEdgeMap.get(litKey);
        if(aimEdge!=null){  //连接的方向与HashMap中的同向
            compairResult = compareEdge(aimEdge, literateEdge);
        }else {
            litKey=literateEdge.getDestination().getId()+splitChart+literateEdge.getSource().getId();
            aimEdge=resultEdgeMap.get(litKey);
            if(aimEdge!=null){//连接的方向与HashMap中的反向
                compairResult = compareEdge(aimEdge, literateEdge);
            }
            else{//HashMap中没找到
                reedgeList.add(literateEdge);
                resultEdgeMap.put(literateEdge.getSource().getId()+splitChart+literateEdge.getDestination().getId(),literateEdge);
                return;
            }
        }
        // 0：同向；1：反向；2：无关
        if (compairResult == 1) {
            if (aimEdge.getStrength() >= literateEdge.getStrength()) {
                aimEdge.setStrength(aimEdge.getStrength() - literateEdge.getStrength());

            } else {
                //反向强度大于正向强度时，起点、终点换方向
                resultEdgeMap.remove(aimEdge.getSource().getId()+splitChart+aimEdge.getDestination().getId()); //删除原先key项
                aimEdge.setDestination(literateEdge.getDestination());
                aimEdge.setSource(literateEdge.getSource());
                aimEdge.setStrength(literateEdge.getStrength() - aimEdge.getStrength());
                resultEdgeMap.put(aimEdge.getSource().getId()+splitChart+aimEdge.getDestination().getId(),aimEdge);

            }
        } else {
            aimEdge.setStrength(literateEdge.getStrength() + aimEdge.getStrength());
        }
        aimEdge.setCount(aimEdge.getCount()+literateEdge.getCount());//边的次数做叠加

    }


    /**
     *比较大小  //没有调用
     * @param v1 被比较值
     * @param v2  比较值 区间的最大值
     * @param v3   比较值  区间的最小值
     * @return   若v1大于v2小于v3 返回true，否则返回false
     */

    public static boolean compare(String v1,String v2,String v3){
        BigDecimal b1 = new BigDecimal(v1);
        BigDecimal b2 = new BigDecimal(v2);
        BigDecimal b3 = new BigDecimal(v3);
        int bj = b1.compareTo(b2);
        int bj1 = b1.compareTo(b3);
        boolean res;
        if(bj >= 0 && bj1 <= 0 ){ //强度值在[v2，v3]之间
            res = true;
        }else{
            res = false;
        }
        return res;
    }
}
